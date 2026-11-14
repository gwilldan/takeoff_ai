"""
Extraction pipeline — walk a drawing set page by page and annotate the plans.

Entry point: extract_pdf(pdf_path, on_page=...) → document result.

Each page runs the same sequence: parse, classify, reconstruct walls, let the
agent validate them, assemble components. Pages are independent, so a result is
emitted as each one finishes rather than at the end — a 65 sheet set otherwise
means a long wait with nothing to show. The `on_page` callback is what carries
that partial result out to the job record.

A document with no measurable plan is not an error. It completes with
`accepted: false` and a reason, because the pipeline did its job and the input
simply was not a construction plan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config import EXTRACTION_CACHE_DIR
from extractor.cache import ExtractionCache, get_page_count, parse_pdf_to_cache
from extractor.classify import classify_page
from extractor.components import build_components
from extractor.graph import build_walls
from extractor.grid import detect_grid, grid_ref_for_point
from extractor.llm import TokenTracker
from extractor.pdf_utils import PdfPageError
from extractor.validate import validate_walls

OnPage = Callable[[dict], None]

NOT_A_PLAN_MESSAGE = (
    "This document does not contain a construction plan. Takeoff AI reads "
    "scaled construction drawings — floor plans, site plans and setting-out "
    "plans. Schedules, specifications and text-only documents cannot be measured."
)


def _wall_colors(profile: dict) -> tuple[list[list[float]], list[list[float]]]:
    wall_info = profile.get("wall_indicators") or {}
    grid_info = profile.get("grid_indicators") or {}

    include = wall_info.get("likely_colors") or [[0, 0, 0]]
    exclude = grid_info.get("likely_colors") or []

    return include, exclude


def _stroke_bounds(profile: dict) -> tuple[float, float | None]:
    stroke_range = (profile.get("wall_indicators") or {}).get("likely_stroke_range")

    if not stroke_range:
        return 0.0, None

    minimum = float(stroke_range[0]) if stroke_range else 0.0
    maximum = float(stroke_range[1]) if len(stroke_range) > 1 else None

    return minimum, maximum


def _skipped_page(cache: ExtractionCache, profile: dict) -> dict:
    return {
        "pageNumber": cache.page_number,
        "isPlan": False,
        "planType": profile.get("plan_type", "other"),
        "skippedReason": profile.get("not_plan_reason") or "not a measurable plan",
        "classifiedBy": profile.get("classified_by", "vision"),
        "pageSize": {"width": cache.page_width_pts, "height": cache.page_height_pts},
        "components": [],
    }


def process_page(
    pdf_path: str,
    page_index: int,
    tracker: TokenTracker,
    *,
    validate: bool = True,
) -> dict:
    """Run one page end to end and return its result, plan or not."""
    cache = parse_pdf_to_cache(pdf_path, page_index=page_index)
    profile = classify_page(cache, tracker)

    if not profile.get("is_plan"):
        return _skipped_page(cache, profile)

    scale_ratio = float(profile.get("scale_ratio") or 100)
    include_colors, exclude_colors = _wall_colors(profile)
    min_stroke, max_stroke = _stroke_bounds(profile)

    grid = detect_grid(cache)
    nodes, walls = build_walls(
        cache.line_segments,
        scale_ratio=scale_ratio,
        include_colors=include_colors,
        exclude_colors=exclude_colors,
        min_stroke=min_stroke,
        max_stroke=max_stroke,
    )

    notes = list(profile.get("notes") or [])

    if validate:
        walls, validation_notes = validate_walls(
            pdf_path, page_index, cache.page_number, walls, tracker
        )
        notes.extend(validation_notes)
        # Nodes are rebuilt from the surviving walls so the graph has no
        # dangling references to rejected candidates.
        if walls:
            from extractor.graph import points_to_nodes

            endpoints = [w.start for w in walls] + [w.end for w in walls]
            nodes, index_map = points_to_nodes(endpoints)
            for wall in walls:
                wall.start_node = index_map[wall.start]
                wall.end_node = index_map[wall.end]
        else:
            nodes = []

    components = build_components(cache, profile, nodes, walls, grid)

    if not grid.is_present:
        notes.append("No setting-out grid detected — components carry no grid references")

    return {
        "pageNumber": cache.page_number,
        "isPlan": True,
        "planType": profile.get("plan_type", "unknown"),
        "classifiedBy": profile.get("classified_by", "vision"),
        "pageSize": {"width": cache.page_width_pts, "height": cache.page_height_pts},
        "scale": {
            "text": profile.get("scale", "unknown"),
            "ratio": scale_ratio,
            "confidence": profile.get("confidence", "medium"),
        },
        "grid": grid.to_dict(),
        "nodes": [
            node.to_dict(grid_ref_for_point(grid, node.x, node.y)) for node in nodes
        ],
        "components": [component.to_dict() for component in components],
        "counts": _counts(components),
        "notes": notes,
    }


def _counts(components) -> dict[str, int]:
    counts: dict[str, int] = {}
    for component in components:
        counts[component.layer] = counts.get(component.layer, 0) + 1
    return counts


def extract_pdf(
    pdf_path: str,
    *,
    on_page: OnPage | None = None,
    write_cache: bool = False,
    validate: bool = True,
) -> dict:
    """
    Annotate every construction plan in a document.

    `on_page` is called with each page result as it completes, which is what lets
    the frontend draw page 1 while page 40 is still running.
    """
    resolved = str(Path(pdf_path).resolve())
    tracker = TokenTracker()

    try:
        page_count = get_page_count(resolved)
    except PdfPageError as exc:
        raise ValueError(str(exc)) from exc

    print(f"[pipeline] {resolved} — {page_count} page(s)")

    pages: list[dict] = []

    for page_index in range(page_count):
        try:
            page_result = process_page(resolved, page_index, tracker, validate=validate)
        except PdfPageError as error:
            page_result = {
                "pageNumber": page_index + 1,
                "isPlan": False,
                "skippedReason": f"page could not be read: {error}",
                "components": [],
            }

        pages.append(page_result)

        if page_result.get("isPlan"):
            counts = page_result.get("counts") or {}
            print(f"[pipeline] page {page_result['pageNumber']}: plan — {counts}")
        else:
            print(
                f"[pipeline] page {page_result['pageNumber']}: skipped — "
                f"{page_result.get('skippedReason')}"
            )

        if on_page is not None:
            on_page(page_result)

        if write_cache:
            cache_path = (
                Path(EXTRACTION_CACHE_DIR)
                / f"{Path(resolved).stem}-p{page_index + 1}.json"
            )
            parse_pdf_to_cache(resolved, page_index=page_index).write_json(cache_path)

    plan_pages = [p for p in pages if p.get("isPlan")]
    accepted = bool(plan_pages)

    result = {
        "accepted": accepted,
        "document": {
            "pdfPath": resolved,
            "pageCount": page_count,
            "planPageCount": len(plan_pages),
        },
        "pages": pages,
        "totals": _document_totals(plan_pages),
        "tokenUsage": tracker.to_dict(),
        "extractedAt": datetime.now(timezone.utc).isoformat(),
    }

    if not accepted:
        result["rejection"] = {
            "reason": "not_a_construction_plan",
            "message": NOT_A_PLAN_MESSAGE,
        }

    print(
        f"[pipeline] done — {len(plan_pages)}/{page_count} plan page(s), "
        f"total_tokens={tracker.total_tokens}"
    )

    return result


def _document_totals(plan_pages: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = {}

    for page in plan_pages:
        for layer, count in (page.get("counts") or {}).items():
            totals[layer] = totals.get(layer, 0) + count

    return totals
