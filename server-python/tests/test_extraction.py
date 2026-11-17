"""
Extraction against real drawings in tests/files.

The CCVE ground floor (raster wall tiles, tagged rooms and openings) is the
reference sheet; the others prove the pipeline is not tuned to it: an
ArchiCAD export whose stated scale is wrong for the sheet, a sheet with no
scale and no labels at all.
"""

from pathlib import Path

import numpy as np
import pytest

from app.extractor.cache import ExtractionCache, parse_pdf_to_cache
from app.extractor.calibrate import check_scale
from app.extractor.classify import detect_scale, plan_region_mask
from app.extractor.grid import detect_grid
from app.extractor.labelling import label_components
from app.extractor.llm import TokenTracker
from app.extractor.openings import Opening, detect_openings
from app.extractor.pipeline import process_page
from app.extractor.raster import PageRaster, orthogonalize
from app.extractor.rooms import UNNAMED, Room, detect_rooms, pair_room_tags, share_nodes
from app.extractor.walls import read_walls, wall_segments

FILES = Path(__file__).parent / "files"
PLAN = FILES / "PROJECT_CCVE_without_first_two_pages.pdf"
ARCHICAD = FILES / "36472-1._architectural_final.pdf"
UNLABELLED = FILES / "Eng copy.pdf"
SCALE = 100.0


def needs(path: Path):
    return pytest.mark.skipif(not path.exists(), reason=f"{path.name} not present")


@pytest.fixture(scope="module")
def ccve():
    cache = parse_pdf_to_cache(str(PLAN), 0)
    raster = PageRaster.from_cache(cache, SCALE)
    grid = detect_grid(cache)
    reading = read_walls(cache, raster, [(t.x, t.y, t.area_m2) for t in pair_room_tags(cache.text_spans)])
    rooms = detect_rooms(cache, raster, reading, grid, SCALE)
    return cache, raster, grid, reading, rooms


def _plan_profile(cache, tracker):
    text, ratio = detect_scale(cache.full_text)
    return {"is_plan": True, "plan_type": "ground_floor_plan", "scale": text or "unknown", "scale_ratio": ratio,
            "confidence": "high", "notes": [], "classified_by": "test"}


# --- walls and rooms ---------------------------------------------------------

@needs(PLAN)
def test_walls_enclose_rooms_without_reading_labels(ccve):
    cache, raster, *_ = ccve
    reading = read_walls(cache, raster, tags=[])  # no labels at all
    assert len(reading.pockets) >= 20
    assert 150 <= sum(p.area_m2 for p in reading.pockets) <= 300


@needs(PLAN)
def test_every_tag_is_accounted_for(ccve):
    cache, *_, rooms = ccve
    assert len(pair_room_tags(cache.text_spans)) == 27
    assert sum(1 for r in rooms if r.area_m2) == 27
    assert len(rooms) >= 27  # plus rooms the drawing does not label


@needs(PLAN)
def test_outlines_match_the_printed_areas(ccve):
    *_, rooms = ccve
    traced = [r for r in rooms if r.area_m2 and r.confidence != "low"]
    within = [r for r in traced if abs(r.measured_area_m2 - r.area_m2) <= max(0.12 * r.area_m2, 0.5)]
    assert len(traced) >= 22 and len(within) >= 22
    assert all(abs(r.measured_area_m2 - r.area_m2) <= 0.3 * r.area_m2 for r in traced)


@needs(PLAN)
def test_rooms_are_mostly_wall_bounded_and_square(ccve):
    *_, rooms = ccve
    assert sum(1 for r in rooms if r.source == "walls") >= 18
    assert sum(1 for r in rooms if len(r.polyline) == 4) >= 18
    diagonals = sum(
        1 for r in rooms
        for (x1, y1), (x2, y2) in zip(r.polyline, r.polyline[1:] + r.polyline[:1])
        if x1 != x2 and y1 != y2
    )
    assert diagonals <= 6


@needs(PLAN)
def test_adjoining_rooms_share_corner_nodes(ccve):
    *_, grid, _, rooms = ccve
    nodes = share_nodes(rooms, grid)
    ids = {n["id"] for n in nodes}
    assert all(set(r.nodes) <= ids and len(r.nodes) == len(r.polyline) for r in rooms)
    assert len(nodes) < sum(len(r.polyline) for r in rooms)
    assert all(n["gridRef"] for n in nodes)


@needs(PLAN)
def test_wall_segments_have_wall_thickness(ccve):
    _, raster, _, reading, _ = ccve
    walls = wall_segments(reading, raster)
    assert len(walls) >= 40
    thicknesses = sorted(w.thickness_mm for w in walls)
    assert 150 <= thicknesses[len(thicknesses) // 2] <= 300  # a 225 mm block wall, give or take
    assert all(len(w.polyline) == 4 and w.length_mm >= 300 for w in walls)


@needs(PLAN)
def test_openings_are_measured_in_their_walls(ccve):
    cache, raster, grid, reading, rooms = ccve
    openings = detect_openings(cache, raster, reading, rooms, grid)
    marked = [o for o in openings if o.reference]
    assert sum(o.kind == "door" for o in marked) == 16 and sum(o.kind == "window" for o in marked) == 16
    measured = [o for o in marked if o.confidence == "high"]
    assert len(measured) >= 24
    assert all(400 <= o.width_mm <= 1600 for o in measured)
    assert sum(1 for o in measured if o.room_ids) >= 20
    for o in measured:  # a span lies along one wall: horizontal or vertical
        (x1, y1), (x2, y2) = o.polyline
        assert abs(x1 - x2) < 0.01 or abs(y1 - y2) < 0.01


@needs(PLAN)
def test_page_result_shape_and_layer_order(monkeypatch):
    monkeypatch.setattr("app.extractor.pipeline.classify_page", _plan_profile)
    page = process_page(str(PLAN), 0, TokenTracker())
    assert page["isPlan"] and page["counts"]["rooms"] >= 27 and page["counts"]["walls"] >= 40
    assert page["counts"]["doors"] >= 16 and page["counts"]["windows"] >= 16
    assert page["tokenUsage"]["totals"]["total_tokens"] == 0
    assert page["scale"]["ratio"] == 100 and page["scale"]["confidence"] == "high"
    layers = [c["layer"] for c in page["components"]]
    # Back to front: rooms, then walls, then doors and windows on top.
    assert layers.index("walls") > max(i for i, l in enumerate(layers) if l == "rooms")
    assert min(i for i, l in enumerate(layers) if l in ("doors", "windows")) > max(i for i, l in enumerate(layers) if l == "walls")
    room = page["components"][0]
    assert room["nodes"] and room["polyline"] and room["source"] in ("walls", "label")


# --- scale -------------------------------------------------------------------

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


@needs(UNLABELLED)
def test_unlabelled_plan_still_yields_rooms_and_door_swings(monkeypatch):
    monkeypatch.setattr("app.extractor.pipeline.classify_page", _plan_profile)
    page = process_page(str(UNLABELLED), 0, TokenTracker())
    rooms = [c for c in page["components"] if c["layer"] == "rooms"]
    assert len(rooms) >= 5 and all(r["source"] == "walls" for r in rooms)
    assert page["counts"].get("doors", 0) >= 4


# --- model steps -------------------------------------------------------------

def test_plan_regions_become_a_padded_mask():
    cache = ExtractionCache(pdf_path="x", page_index=0, page_width_pts=1000, page_height_pts=500)
    raster = PageRaster(px_per_pt=1, pt_per_mm=1, barrier=np.zeros((500, 1000), np.uint8), ink=np.zeros((500, 1000), np.uint8))
    mask = plan_region_mask([[100, 100, 400, 600], ["bad"], [900, 900, 800, 950]], cache, raster)
    assert mask[300, 300] == 1 and mask[300, 900] == 0
    assert mask[45, 85] == 1  # widened by the margin
    assert plan_region_mask(None, cache, raster) is None


def test_labelling_names_rooms_and_rejects_without_overruling_the_drawing(monkeypatch):
    unnamed = Room("room-001", UNNAMED, [[0, 0], [10, 0], [10, 10], [0, 10]], "medium", [], "walls")
    named = Room("room-002", "KITCHEN", [[20, 0], [30, 0], [30, 10], [20, 10]], "high", [], "walls", area_m2=12)
    stray = Room("room-003", UNNAMED, [[40, 0], [50, 0], [50, 10], [40, 10]], "medium", [], "walls")
    door = Opening("", "door", [[10, 5], [20, 5]], 900, "medium", room_ids=["room-001", "room-003"])
    gap = Opening("", "door", [[30, 5], [40, 5]], 900, "medium")
    tagged = Opening("", "window", [[0, 20], [10, 20]], 1200, "high", reference="W1")
    answer = {"rooms": {"R1": {"name": "Bedroom", "is_room": True}, "R2": {"name": "STORE", "is_room": False},
                        "R3": {"name": "STAIR", "is_room": False}},
              "openings": {"O1": "door", "O2": "not_an_opening", "O3": "not_an_opening"}}
    monkeypatch.setattr("app.extractor.labelling._ask", lambda *args: answer)

    rooms, openings = label_components(None, TokenTracker(), [unnamed, named, stray], [door, gap, tagged])
    assert [r.name for r in rooms] == ["BEDROOM", "KITCHEN"]  # the drawing's own name is kept
    assert "doubts" in rooms[1].notes[-1]
    assert [o.reference for o in openings] == [None, "W1"]  # a tagged opening is never dropped
    assert openings[0].room_ids == ["room-001"] and openings[0].id == "door-001"


# --- prefilter and geometry helpers ------------------------------------------

@needs(PLAN)
def test_elevation_sheet_is_skipped_before_the_model():
    page = process_page(str(PLAN), 2, TokenTracker())
    assert page["isPlan"] is False and page["classifiedBy"] == "prefilter"
    assert "section or elevation" in page["skippedReason"]


@needs(PLAN)
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


def test_pair_room_tags_reads_mixed_case_names_and_split_superscripts():
    # "Pool Office" in 15 pt lettering, its area "41 M" (the ² a separate span) three lines below.
    spans = [{"text": "Pool Office", "cx": 780, "cy": 410, "font_size": 15.6}, {"text": "41 M", "cx": 775, "cy": 457}]
    assert [(t.name, t.area_m2) for t in pair_room_tags(spans)] == [("POOL OFFICE", 41.0)]
