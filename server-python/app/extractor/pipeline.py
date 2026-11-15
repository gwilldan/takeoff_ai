"""
One page end to end: parse → classify → rooms → doors and windows.

A page that is not a plan is not an error; it completes as skipped with a
reason so the frontend can list it. A document with no plan page completes
with `accepted: false` (see `tasks.finalize_extraction`).
"""

from __future__ import annotations

import logging

from app.extractor.cache import parse_pdf_to_cache
from app.extractor.classify import classify_page
from app.extractor.grid import detect_grid
from app.extractor.llm import TokenTracker
from app.extractor.openings import detect_openings
from app.extractor.raster import PageRaster
from app.extractor.rooms import detect_rooms

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

    scale_ratio = float(profile.get("scale_ratio") or 100)
    grid = detect_grid(cache)
    raster = PageRaster.from_cache(cache, scale_ratio)
    nodes, rooms = detect_rooms(cache, raster, grid, scale_ratio)
    # Openings live in room walls. A page with no traced room (a schedule that
    # happens to list door tags, say) gets none.
    traced = any(room.confidence != "low" for room in rooms)
    openings = detect_openings(cache, raster, rooms, grid, scale_ratio) if traced else []
    components = [c.to_dict() for c in (*rooms, *openings)]

    notes = list(profile.get("notes") or [])
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
        "scale": {"text": profile.get("scale", "unknown"), "ratio": scale_ratio, "confidence": profile.get("confidence", "medium")},
        "grid": grid.to_dict(),
        "nodes": nodes,
        "components": components,
        "counts": counts,
        "notes": notes,
    }
