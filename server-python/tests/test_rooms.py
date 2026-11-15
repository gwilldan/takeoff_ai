"""Room detection against a real drawing: tests/files/PROJECT_CCVE_without_first_two_pages.pdf, page 1."""

from pathlib import Path

import pytest

from app.extractor.cache import ExtractionCache, parse_pdf_to_cache
from app.extractor.grid import detect_grid
from app.extractor.llm import TokenTracker
from app.extractor.openings import detect_openings
from app.extractor.pipeline import process_page
from app.extractor.raster import PageRaster, orthogonalize
from app.extractor.rooms import detect_rooms, pair_room_tags

PLAN = Path(__file__).parent / "files" / "PROJECT_CCVE_without_first_two_pages.pdf"
SCALE = 100.0

pytestmark = pytest.mark.skipif(not PLAN.exists(), reason="sample drawing not present")


@pytest.fixture(scope="module")
def page_rooms():
    cache = parse_pdf_to_cache(str(PLAN), 0)
    raster = PageRaster.from_cache(cache, SCALE)
    nodes, rooms = detect_rooms(cache, raster, detect_grid(cache), SCALE)
    return cache, raster, nodes, rooms


def test_every_tag_becomes_a_room(page_rooms):
    cache, _, _, rooms = page_rooms
    assert len(pair_room_tags(cache.text_spans)) == 27
    assert len(rooms) == 27
    assert all(room.area_m2 for room in rooms)


def test_outlines_match_the_printed_areas(page_rooms):
    _, _, _, rooms = page_rooms
    traced = [r for r in rooms if r.confidence != "low"]
    within = [r for r in traced if abs(r.measured_area_m2 - r.area_m2) <= max(0.12 * r.area_m2, 0.5)]
    assert len(traced) >= 23
    assert len(within) >= 22
    assert all(abs(r.measured_area_m2 - r.area_m2) <= 0.3 * r.area_m2 for r in traced)


def test_outlines_are_orthogonal_and_lean(page_rooms):
    _, _, _, rooms = page_rooms
    rectangles = [r for r in rooms if len(r.polyline) == 4]
    assert len(rectangles) >= 15
    # Door swings still leave the odd stair-step; real splayed walls are rarer still.
    diagonals = sum(
        1 for r in rooms
        for (x1, y1), (x2, y2) in zip(r.polyline, r.polyline[1:] + r.polyline[:1])
        if x1 != x2 and y1 != y2
    )
    assert diagonals <= 6


def test_adjoining_rooms_share_corner_nodes(page_rooms):
    _, _, nodes, rooms = page_rooms
    ids = {n["id"] for n in nodes}
    assert all(set(r.nodes) <= ids and len(r.nodes) == len(r.polyline) for r in rooms)
    assert len(nodes) < sum(len(r.polyline) for r in rooms)
    assert all(n["gridRef"] for n in nodes)


def test_page_result_shape(monkeypatch):
    monkeypatch.setattr(
        "app.extractor.pipeline.classify_page",
        lambda cache, tracker: {"is_plan": True, "plan_type": "ground_floor_plan", "scale": "1:100",
                                "scale_ratio": 100, "confidence": "high", "notes": [], "classified_by": "test"},
    )
    page = process_page(str(PLAN), 0, TokenTracker())
    assert page["isPlan"] and page["counts"]["rooms"] == 27
    assert page["counts"]["doors"] == 16 and page["counts"]["windows"] == 16
    assert page["tokenUsage"]["totals"]["total_tokens"] == 0
    assert page["scale"] == {"text": "1:100", "ratio": 100.0, "confidence": "high"}
    room = page["components"][0]
    assert room["layer"] == "rooms" and room["nodes"] and room["polyline"]
    assert {n["id"] for n in page["nodes"]} >= set(room["nodes"])


def test_openings_sit_on_room_walls_with_measured_widths(page_rooms):
    cache, raster, _, rooms = page_rooms
    openings = detect_openings(cache, raster, rooms, detect_grid(cache), SCALE)
    doors = [o for o in openings if o.kind == "door"]
    windows = [o for o in openings if o.kind == "window"]
    assert len(doors) == 16 and len(windows) == 16
    measured = [o for o in openings if o.confidence == "high"]
    assert len(measured) >= 24
    assert all(o.host_room_id for o in measured)
    assert all(400 <= o.width_mm <= 1300 for o in measured)
    for o in measured:  # a span lies along one wall: horizontal or vertical
        (x1, y1), (x2, y2) = o.polyline
        assert abs(x1 - x2) < 0.01 or abs(y1 - y2) < 0.01


def test_elevation_sheet_is_skipped_before_the_model():
    page = process_page(str(PLAN), 2, TokenTracker())
    assert page["isPlan"] is False and page["classifiedBy"] == "prefilter"
    assert "section or elevation" in page["skippedReason"]


def test_page_with_nothing_drawn_is_skipped_before_the_model(monkeypatch):
    blank = ExtractionCache(pdf_path=str(PLAN), page_index=6, page_width_pts=1191, page_height_pts=842,
                            full_text="DRAWING REGISTER", line_segments=[{"stroke_width": 0.3}] * 10)
    monkeypatch.setattr("app.extractor.pipeline.parse_pdf_to_cache", lambda path, index: blank)
    page = process_page(str(PLAN), 6, TokenTracker())
    assert page["isPlan"] is False and page["classifiedBy"] == "prefilter"


def test_orthogonalize_squares_a_nearly_rectangular_trace():
    traced = [[0, 0], [100, 0.8], [100.5, 50], [60, 50.4], [60.2, 50.1], [0.3, 50]]
    squared = orthogonalize(traced)
    assert len(squared) == 4
    for (x1, y1), (x2, y2) in zip(squared, squared[1:] + squared[:1]):
        assert x1 == x2 or y1 == y2


def test_orthogonalize_keeps_a_real_splay():
    splayed = [[0, 0], [100, 0], [100, 60], [40, 60], [0, 20]]
    assert orthogonalize(splayed) == [[0.0, 0.0], [100.0, 0.0], [100.0, 60.0], [40.0, 60.0], [0.0, 20.0]]


def test_scanned_page_is_read_with_ocr(tmp_path):
    pytest.importorskip("fitz")
    import fitz, shutil
    if not shutil.which("tesseract"):
        pytest.skip("tesseract not installed")
    source = fitz.open(str(PLAN))[0]
    scan = fitz.open(); page = scan.new_page(width=source.rect.width, height=source.rect.height)
    page.insert_image(page.rect, pixmap=source.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False))
    path = tmp_path / "scan.pdf"; scan.save(str(path))

    cache = parse_pdf_to_cache(str(path), 0)
    assert cache.text_source == "ocr" and len(cache.text_spans) > 100
    names = {t.name for t in pair_room_tags(cache.text_spans)}
    assert {"BEDROOM", "KITCHEN", "TOILET"} <= names


def test_pair_room_tags_matches_each_area_to_its_nearest_name():
    spans = [
        {"text": "BEDROOM", "cx": 100, "cy": 100}, {"text": "12 m²", "cx": 100, "cy": 110},
        {"text": "STORE", "cx": 140, "cy": 100}, {"text": "3 m²", "cx": 140, "cy": 110},
        {"text": "NOTES", "cx": 400, "cy": 400}, {"text": "LOBBY", "cx": 300, "cy": 300},
    ]
    tags = {(t.name, t.area_m2) for t in pair_room_tags(spans)}
    assert tags == {("BEDROOM", 12.0), ("STORE", 3.0), ("LOBBY", None)}
