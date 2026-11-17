"""
One page end to end:

parse → classify (vision: is it a plan, where are the plan viewports, what
scale) → check the scale against the printed dimensions → read the walls →
rooms → doors and windows → wall segments → name and verify (vision, the
components numbered on the page) → share corner nodes.

Geometry is deterministic; the model reads and names, it never places a
point. Every model step degrades to the deterministic result when the model
is unreachable.

A page that is not a plan is not an error; it completes as skipped with a
reason so the frontend can list it. A document with no plan page completes
with `accepted: false` (see `tasks.finalize_extraction`).
"""

from __future__ import annotations

import logging

from app.extractor.cache import parse_pdf_to_cache
from app.extractor.calibrate import check_scale
from app.extractor.classify import classify_page, plan_region_mask
from app.extractor.grid import detect_grid
from app.extractor.labelling import label_components
from app.extractor.llm import TokenTracker
from app.extractor.openings import detect_openings
from app.extractor.raster import PageRaster
from app.extractor.rooms import detect_rooms, pair_room_tags, share_nodes
from app.extractor.walls import read_walls, wall_segments

log = logging.getLogger(__name__)

NOT_A_PLAN_MESSAGE = (
    "This document does not contain a construction plan. Takeoff AI reads "
    "scaled construction drawings — floor plans, site plans and setting-out "
    "plans. Schedules, specifications and text-only documents cannot be measured."
)


def process_page(pdf_path: str, page_index: int, tracker: TokenTracker) -> dict:
    cache = parse_pdf_to_cache(pdf_path, page_index)
    profile = classify_page(cache, tracker)

    page = {
        "pageNumber": cache.page_number,
        "isPlan": bool(profile.get("is_plan")),
        "planType": profile.get("plan_type", "other"),
        "classifiedBy": profile.get("classified_by", "vision"),
        "textSource": cache.text_source,
        "pageSize": {"width": cache.page_width_pts, "height": cache.page_height_pts},
        "components": [],
        "tokenUsage": tracker.to_dict(),
    }

    if not page["isPlan"]:
        page["skippedReason"] = profile.get("not_plan_reason") or "not a measurable plan"
        log.info("page %d: skipped — %s", cache.page_number, page["skippedReason"])
        return page

    scale = check_scale(cache, profile)
    grid = detect_grid(cache)
    raster = PageRaster.from_cache(cache, scale.ratio)
    tags = [(t.x, t.y, t.area_m2) for t in pair_room_tags(cache.text_spans)]
    reading = read_walls(cache, raster, tags, plan_region_mask(profile.get("plan_regions"), cache, raster))
    rooms = detect_rooms(cache, raster, reading, grid, scale.ratio)
    openings = detect_openings(cache, raster, reading, rooms, grid)
    walls = wall_segments(reading, raster)
    rooms, openings = label_components(cache, tracker, rooms, openings)
    nodes = share_nodes(rooms, grid)
    # Back to front: rooms and walls behind, doors and windows on top.
    components = [c.to_dict() for c in (*rooms, *walls, *openings)]

    notes = list(profile.get("notes") or []) + scale.notes + [f"Walls read as: {reading.name}"]
    if not grid.is_present:
        notes.append("No setting-out grid detected — nodes carry no grid references")
    if cache.text_source == "ocr":
        notes.append("Text read by OCR; printed areas may be missing and names approximate")

    counts: dict[str, int] = {}
    for component in components:
        counts[component["layer"]] = counts.get(component["layer"], 0) + 1

    log.info("page %d: plan — %s", cache.page_number, counts)
    return {
        **page,
        "scale": scale.to_dict(),
        "grid": grid.to_dict(),
        "nodes": nodes,
        "components": components,
        "counts": counts,
        "notes": notes,
        "tokenUsage": tracker.to_dict(),
    }
