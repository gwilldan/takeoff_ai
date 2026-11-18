"""
One page, in two halves around the QuantTakeoff call.

`read_page` runs before it: parse → classify (the only model call: is this a
floor plan?) → check the scale against the printed dimensions → read the
setting-out grid. Only floor plans go on to QuantTakeoff.

`complete_page` runs after it: QuantTakeoff's page result, unchanged, plus our
fields — the scale we measure with, grid snap lines and wall lengths. Every
field QuantTakeoff sends keeps its name and meaning, so anything that reads
their format reads ours. Door and window marks are theirs, in `name`.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import cv2
import numpy as np

from app.extractor.cache import parse_pdf_to_cache
from app.extractor.calibrate import MM_PER_PT, check_scale
from app.extractor.classify import classify_page
from app.extractor.grid import detect_grid
from app.extractor.llm import TokenTracker

log = logging.getLogger(__name__)

NOT_A_PLAN_MESSAGE = (
    "This document does not contain a floor plan. Takeoff AI measures floor "
    "plans; sections, elevations, schedules and text-only sheets cannot be measured."
)

# QuantTakeoff measures floor plans only: site and roof plans are not sent.
FLOOR_PLAN_TYPES = {"ground_floor_plan", "upper_floor_plan", "floor_plan", "unknown"}

# QuantTakeoff scale sources that come from the drawing rather than a guess.
MEASURED_SCALE_SOURCES = {"user", "drawing", "dimensions"}


def read_page(pdf_path: str, page_index: int, tracker: TokenTracker) -> dict[str, Any]:
    """Everything known about a page before QuantTakeoff sees it."""
    started = time.monotonic()
    cache = parse_pdf_to_cache(pdf_path, page_index)
    profile = classify_page(cache, tracker)
    plan_type = profile.get("plan_type", "other")
    is_plan = bool(profile.get("is_plan")) and plan_type in FLOOR_PLAN_TYPES

    page: dict[str, Any] = {
        "page_index": page_index,
        "is_plan": is_plan,
        "plan_type": plan_type,
        "classified_by": profile.get("classified_by", "vision"),
        "text_source": cache.text_source,
        "page_size_pt": {"width": cache.page_width_pts, "height": cache.page_height_pts},
        "detections": [],
        "notes": list(profile.get("notes") or []),
        "token_usage": tracker.to_dict(),
    }
    if not is_plan:
        page["skipped_reason"] = (
            profile.get("not_plan_reason")
            or (f"{plan_type.replace('_', ' ')} is not a floor plan" if profile.get("is_plan") else "not a floor plan")
        )
    else:
        scale = check_scale(cache, profile)
        page["scale_check"] = scale.to_dict()
        page["notes"] += scale.notes
        page["grid"] = detect_grid(cache).to_dict()

    page["timings"] = {"read_s": round(time.monotonic() - started, 2)}
    return page


def complete_page(page: dict[str, Any], result_page: dict[str, Any], takeoff_metrics: dict[str, Any] | None) -> dict[str, Any]:
    """
    QuantTakeoff's page with our fields added; nothing of theirs renamed or
    dropped. The page was sent on its own, so their `page_index` 0 is ours.
    """
    px_per_pt = result_page["image_size"]["width"] / page["page_size_pt"]["width"]
    theirs = (result_page.get("scale") or {}).get("meters_per_pixel") or 0
    metres_per_px, scale_used = _metres_per_px(page, result_page, px_per_pt)
    # Their areas are at their scale; when ours replaced a guess, say what they come to at ours.
    area_factor = (metres_per_px / theirs) ** 2 if theirs and metres_per_px and abs(metres_per_px / theirs - 1) > 0.01 else None

    detections = []
    for detection in result_page.get("detections") or []:
        detection = dict(detection)
        if detection["class_name"] == "wall" and metres_per_px:
            detection["length_m"] = round(_long_side(detection["polygon"]) * metres_per_px, 3)
        if area_factor and detection.get("area_m2") is not None:
            detection["area_m2_checked"] = round(detection["area_m2"] * area_factor, 2)
        detections.append(detection)

    grid = page.get("grid") or {"columns": [], "rows": []}
    return {
        **page,
        **result_page,
        "page_index": page["page_index"],
        "detections": detections,
        # Grid axes in the same page pixels as the detections, for snapping.
        "grid": {
            "columns": [{"ref": a["ref"], "x": round(a["x"] * px_per_pt, 1)} for a in grid["columns"]],
            "rows": [{"ref": a["ref"], "y": round(a["y"] * px_per_pt, 1)} for a in grid["rows"]],
        },
        "metres_per_px": metres_per_px,
        "scale_used": scale_used,
        "metrics": takeoff_metrics or {},
    }


def _metres_per_px(page: dict[str, Any], result_page: dict[str, Any], px_per_pt: float) -> tuple[float | None, str | None]:
    """
    The scale to measure with, and whose it is. QuantTakeoff's when it read it
    from the drawing; ours when we confirmed or measured it, since theirs is
    then at best an estimate from door widths; otherwise whichever exists.
    """
    theirs = result_page.get("scale") or {}
    their_mpp = theirs.get("meters_per_pixel") or None
    check = page.get("scale_check") or {}
    our_mpp = MM_PER_PT * check["ratio"] / 1000 / px_per_pt if check.get("ratio") else None
    # Confirmed or measured against the printed dimensions; a stated scale nobody checked is not.
    ours_confident = check.get("dimensionsChecked", 0) > 0

    if their_mpp and theirs.get("source") in MEASURED_SCALE_SOURCES:
        return their_mpp, "quanttakeoff"
    if our_mpp and ours_confident:
        return our_mpp, "ours"
    if their_mpp:
        return their_mpp, "quanttakeoff"
    return our_mpp, "ours" if our_mpp else None


def _long_side(polygon: list[list[float]]) -> float:
    """Length of a wall band: the long side of its tightest rotated rectangle."""
    if len(polygon) < 2:
        return 0.0
    if len(polygon) == 2:
        return math.dist(*polygon)
    (_, _), (w, h), _ = cv2.minAreaRect(np.asarray(polygon, dtype=np.float32))
    return float(max(w, h))
