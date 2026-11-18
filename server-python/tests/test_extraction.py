"""
Page reading against real drawings in tests/files, and the merge of a
QuantTakeoff page result with what we read.

The CCVE set is the reference; the ArchiCAD export proves a wrong stated scale
is overruled, and the unlabelled sheet that a missing one is measured.
"""

from pathlib import Path

import pytest

from app.extractor import calibrate
from app.extractor.cache import ExtractionCache, parse_pdf_to_cache
from app.extractor.calibrate import check_scale
from app.extractor.classify import detect_scale
from app.extractor.llm import TokenTracker
from app.extractor.pipeline import complete_page, read_page

FILES = Path(__file__).parent / "files"
PLAN = FILES / "PROJECT_CCVE_without_first_two_pages.pdf"
ARCHICAD = FILES / "36472-1._architectural_final.pdf"
UNLABELLED = FILES / "Eng copy.pdf"


def needs(path: Path):
    return pytest.mark.skipif(not path.exists(), reason=f"{path.name} not present")


def _plan_profile(cache, tracker):
    text, ratio = detect_scale(cache.full_text)
    return {"is_plan": True, "plan_type": "ground_floor_plan", "scale": text or "unknown", "scale_ratio": ratio,
            "confidence": "high", "notes": [], "classified_by": "test"}


# --- before the takeoff --------------------------------------------------------

@needs(PLAN)
def test_floor_plan_is_read_with_checked_scale_and_grid(monkeypatch):
    monkeypatch.setattr("app.extractor.pipeline.classify_page", _plan_profile)
    page = read_page(str(PLAN), 0, TokenTracker())
    assert page["is_plan"] and page["page_index"] == 0 and page["detections"] == []
    assert page["scale_check"]["ratio"] == 100 and page["scale_check"]["confidence"] == "high"
    assert len(page["grid"]["columns"]) >= 5 and len(page["grid"]["rows"]) >= 5
    assert page["timings"]["read_s"] >= 0 and page["token_usage"]["totals"]["total_tokens"] == 0


@needs(PLAN)
def test_site_and_roof_plans_are_not_sent_for_takeoff(monkeypatch):
    monkeypatch.setattr("app.extractor.pipeline.classify_page",
                        lambda cache, tracker: {**_plan_profile(cache, tracker), "plan_type": "roof_plan"})
    page = read_page(str(PLAN), 0, TokenTracker())
    assert page["is_plan"] is False and page["skipped_reason"] == "roof plan is not a floor plan"


@needs(PLAN)
def test_elevation_sheet_is_skipped_before_the_model():
    page = read_page(str(PLAN), 2, TokenTracker())
    assert page["is_plan"] is False and page["classified_by"] == "prefilter"
    assert "section or elevation" in page["skipped_reason"]


@needs(PLAN)
def test_page_with_nothing_drawn_is_skipped_before_the_model(monkeypatch):
    blank = ExtractionCache(pdf_path=str(PLAN), page_index=6, page_width_pts=1191, page_height_pts=842,
                            full_text="DRAWING REGISTER", line_segments=[{"stroke_width": 0.3}] * 10)
    monkeypatch.setattr("app.extractor.pipeline.parse_pdf_to_cache", lambda path, index: blank)
    page = read_page(str(PLAN), 6, TokenTracker())
    assert page["is_plan"] is False and page["classified_by"] == "prefilter"


@needs(PLAN)
def test_scanned_page_is_read_with_ocr(tmp_path):
    import shutil

    import fitz
    if not shutil.which("tesseract"):
        pytest.skip("tesseract not installed")
    source = fitz.open(str(PLAN))[0]
    scan = fitz.open()
    page = scan.new_page(width=source.rect.width, height=source.rect.height)
    page.insert_image(page.rect, pixmap=source.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False))
    path = tmp_path / "scan.pdf"
    scan.save(str(path))

    cache = parse_pdf_to_cache(str(path), 0)
    assert cache.text_source == "ocr" and len(cache.text_spans) > 100
    assert {"BEDROOM", "KITCHEN", "TOILET"} <= {s["text"] for s in cache.text_spans}


# --- scale ---------------------------------------------------------------------

@needs(PLAN)
def test_stated_scale_is_confirmed_by_the_dimensions():
    cache = parse_pdf_to_cache(str(PLAN), 0)
    scale = check_scale(cache, {"scale": "1:100", "scale_ratio": 100})
    assert scale.ratio == 100 and scale.confidence == "high" and scale.dimensions_checked >= 20


@needs(ARCHICAD)
def test_a_wrong_stated_scale_is_overruled():
    # An A1 sheet whose drawing says 1:100 (set up for A3), dimensioned in centimetres.
    cache = parse_pdf_to_cache(str(ARCHICAD), 1)
    scale = check_scale(cache, {"scale": "1:100", "scale_ratio": 100})
    assert scale.source == "dimensions" and abs(scale.ratio - 50) <= 2


@needs(UNLABELLED)
def test_a_missing_scale_is_measured():
    cache = parse_pdf_to_cache(str(UNLABELLED), 0)
    scale = check_scale(cache, {"scale": "unknown", "scale_ratio": None})
    assert scale.source == "dimensions" and abs(scale.ratio - 50) <= 3


def _span(text: str, cx: float, cy: float = 100.0) -> tuple[dict, float]:
    half = 2.5 * len(text)
    span = {"text": text, "cx": cx, "cy": cy, "x": cx - half, "x2": cx + half, "y": cy - 3, "y2": cy + 3}
    return span, calibrate._value(text)


def test_a_dimension_chain_gives_the_scale_without_its_lines():
    # 2025 | 1125 | 2212 drawn at 1:200: neighbours sit half their sum apart.
    mm_per_pt = 200 * calibrate.MM_PER_PT
    spans = [_span("2025", 50.0), _span("1125", 50 + 1575 / mm_per_pt), _span("2212", 50 + (1575 + 1668.5) / mm_per_pt)]
    readings = calibrate._chain_readings(spans)
    assert len(readings) == 2 and all(abs(r / calibrate.MM_PER_PT - 200) < 0.5 for r in readings)


def test_evenly_spaced_equal_labels_are_not_a_chain():
    # A run of riser heights agrees with itself at any spacing; it proves nothing.
    assert calibrate._chain_readings([_span("18", 10.0 * i) for i in range(1, 8)]) == []


# --- after the takeoff ---------------------------------------------------------

def _takeoff_page() -> dict:
    return {
        "page_index": 0, "image_size": {"width": 2000, "height": 1000},
        "scale": {"meters_per_pixel": 0.01, "denominator": 100, "source": "user", "warnings": []},
        "detections": [
            {"id": "w1", "class_name": "wall", "score": 0.9, "bbox": [0, 0, 500, 20],
             "polygon": [[0, 0], [500, 0], [500, 20], [0, 20]], "coordinate_space": "page_pixels"},
            {"id": "d1", "class_name": "door", "score": 0.8, "bbox": [0, 0, 40, 40], "name": "D1",
             "polygon": [[0, 0], [40, 0], [40, 40], [0, 40]], "coordinate_space": "page_pixels"},
            {"id": "r1", "class_name": "room", "score": 1.0, "bbox": [0, 0, 100, 100], "name": "KITCHEN",
             "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]], "area_m2": 1.0, "coordinate_space": "page_pixels"},
        ],
        "plans": [],
    }


def _ours(**scale_check) -> dict:
    return {"page_index": 5, "is_plan": True, "plan_type": "floor_plan", "page_size_pt": {"width": 1000, "height": 500},
            "scale_check": scale_check, "grid": {"columns": [{"ref": "A", "x": 10.0}], "rows": [{"ref": "1", "y": 20.0}]},
            "detections": [], "timings": {"read_s": 1.0}}


def test_takeoff_page_keeps_every_field_and_gains_ours():
    their_metrics = {"Total wall length": "5.00 m"}
    page = complete_page(_ours(ratio=100, confidence="high", dimensionsChecked=57), _takeoff_page(), their_metrics)

    theirs = _takeoff_page()
    for key in theirs:
        assert key in page
    for mine, original in zip(page["detections"], theirs["detections"]):
        assert {k: mine[k] for k in original} == original

    wall, door, room = page["detections"]
    assert wall["length_m"] == 5.0 and door["name"] == "D1"
    assert room["name"] == "KITCHEN" and "length_m" not in room and "area_m2_checked" not in room
    # The page was sent on its own: their page 0 is our page 5.
    assert page["page_index"] == 5 and page["metrics"] == their_metrics
    # Grid axes move into page pixels: 2 px per point here.
    assert page["grid"] == {"columns": [{"ref": "A", "x": 20.0}], "rows": [{"ref": "1", "y": 40.0}]}
    assert page["is_plan"] and page["metres_per_px"] == 0.01 and page["scale_used"] == "quanttakeoff"


def test_our_measured_scale_replaces_a_door_width_guess():
    # QuantTakeoff guessed 1:200 from door widths; the dimensions say 1:195.
    guessed = {**_takeoff_page(), "scale": {"meters_per_pixel": 0.02, "denominator": 200, "source": "door_widths"}}
    page = complete_page(_ours(ratio=195, source="dimensions", dimensionsChecked=10), guessed, {})
    ours = 195 * calibrate.MM_PER_PT / 1000 / 2  # 2 px per point
    wall, _, room = page["detections"]
    assert page["scale_used"] == "ours" and page["metres_per_px"] == pytest.approx(ours)
    assert wall["length_m"] == pytest.approx(500 * ours, abs=0.001)
    assert room["area_m2"] == 1.0 and room["area_m2_checked"] == pytest.approx((ours / 0.02) ** 2, abs=0.01)


def test_an_unchecked_stated_scale_does_not_replace_their_estimate():
    # The classifier's confidence in what the sheet says is not a check against its dimensions.
    guessed = {**_takeoff_page(), "scale": {"meters_per_pixel": 0.02, "source": "door_widths"}}
    page = complete_page(_ours(ratio=100, source="stated", confidence="high", dimensionsChecked=0), guessed, {})
    assert page["scale_used"] == "quanttakeoff" and page["metres_per_px"] == 0.02


def test_our_scale_measures_walls_when_quanttakeoff_found_none():
    result_page = {**_takeoff_page(), "scale": {"meters_per_pixel": 0, "source": "none"}}
    wall = complete_page(_ours(ratio=100, source="stated"), result_page, {})["detections"][0]
    # 500 px at 2 px/pt is 250 pt of paper, at 1:100 that is 8.82 m.
    assert wall["length_m"] == pytest.approx(250 * calibrate.MM_PER_PT * 100 / 1000, abs=0.001)


@needs(PLAN)
def test_each_floor_plan_is_sent_as_a_pdf_of_its_own():
    import fitz

    from app.takeoff_api import single_page_pdf
    with fitz.open(stream=single_page_pdf(str(PLAN), 1), filetype="pdf") as sent, fitz.open(str(PLAN)) as source:
        assert len(sent) == 1 and sent[0].get_text() == source[1].get_text() and sent[0].rect == source[1].rect
