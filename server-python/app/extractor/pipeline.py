"""
One page end to end: parse → classify → detect rooms.

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
        "pageSize": {"width": cache.page_width_pts, "height": cache.page_height_pts},
        "components": [],
    }

    if not page["isPlan"]:
        page["skippedReason"] = profile.get("not_plan_reason") or "not a measurable plan"
        log.info("page %d: skipped — %s", cache.page_number, page["skippedReason"])
        return page

    scale_ratio = float(profile.get("scale_ratio") or 100)
    grid = detect_grid(cache)
    nodes, rooms = detect_rooms(cache, PageRaster.from_cache(cache), grid, scale_ratio)

    notes = list(profile.get("notes") or [])
    if not grid.is_present:
        notes.append("No setting-out grid detected — nodes carry no grid references")

    log.info("page %d: plan — %d room(s)", cache.page_number, len(rooms))
    return {
        **page,
        "scale": {"text": profile.get("scale", "unknown"), "ratio": scale_ratio, "confidence": profile.get("confidence", "medium")},
        "grid": grid.to_dict(),
        "nodes": nodes,
        "components": [room.to_dict() for room in rooms],
        "counts": {"rooms": len(rooms)} if rooms else {},
        "notes": notes,
    }
