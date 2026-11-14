"""
Query tools — narrow slices from an ExtractionCache.

These replace the bulk dump tools. Each function returns a small, focused
result that is safe to include in LLM context.
"""

from __future__ import annotations

import re
from typing import Optional

from extractor.cache import ExtractionCache
from extractor.spatial import (
    bbox_from_center,
    colors_close,
    distance,
    parse_color_key,
    point_in_bbox,
    point_to_segment_distance,
)


def get_color_summary(cache: ExtractionCache) -> dict:
    ranked = sorted(
        cache.color_summary.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )
    return {
        "colors": [
            {"color_rgb": parse_color_key(k), "path_count": v, "raw_key": k}
            for k, v in ranked[:15]
        ],
        "total_paths": sum(cache.color_summary.values()),
    }


def get_text_by_pattern(cache: ExtractionCache, pattern: str, limit: int = 100) -> list[dict]:
    regex = re.compile(pattern, re.IGNORECASE)
    results = []
    for span in cache.text_spans:
        if regex.search(span["text"]):
            results.append({
                "text": span["text"],
                "position_pts": [span["cx"], span["cy"]],
                "font_size": span["font_size"],
            })
            if len(results) >= limit:
                break
    return results


def get_text_near_point(
    cache: ExtractionCache,
    x: float,
    y: float,
    radius: float = 80,
    limit: int = 20,
) -> list[dict]:
    hits = []
    for span in cache.text_spans:
        d = distance(x, y, span["cx"], span["cy"])
        if d <= radius:
            hits.append({
                "text": span["text"],
                "position_pts": [span["cx"], span["cy"]],
                "distance_pts": round(d, 1),
                "font_size": span["font_size"],
            })
    hits.sort(key=lambda h: h["distance_pts"])
    return hits[:limit]


def get_lines_in_bbox(
    cache: ExtractionCache,
    bbox: list[float],
    limit: int = 100,
) -> list[dict]:
    results = []
    for line in cache.line_segments:
        sx, sy = line["start"]
        ex, ey = line["end"]
        mid_x = (sx + ex) / 2
        mid_y = (sy + ey) / 2
        if point_in_bbox(mid_x, mid_y, bbox) or point_in_bbox(sx, sy, bbox):
            results.append(line)
            if len(results) >= limit:
                break
    return results


def get_lines_by_color(
    cache: ExtractionCache,
    color_rgb: list[float],
    min_stroke: float = 0,
    max_stroke: Optional[float] = None,
    limit: int = 200,
) -> list[dict]:
    results = []
    for line in cache.line_segments:
        if not colors_close(line.get("color_rgb"), color_rgb):
            continue
        sw = line.get("stroke_width") or 0
        if sw < min_stroke:
            continue
        if max_stroke is not None and sw > max_stroke:
            continue
        results.append(line)
        if len(results) >= limit:
            break
    return results


def get_lines_near_point(
    cache: ExtractionCache,
    x: float,
    y: float,
    radius: float = 100,
    limit: int = 30,
) -> list[dict]:
    hits = []
    for line in cache.line_segments:
        sx, sy = line["start"]
        ex, ey = line["end"]
        d = point_to_segment_distance(x, y, sx, sy, ex, ey)
        if d <= radius:
            hits.append({**line, "distance_pts": round(d, 1)})
    hits.sort(key=lambda h: h["distance_pts"])
    return hits[:limit]


def get_curves_near_point(
    cache: ExtractionCache,
    x: float,
    y: float,
    radius: float = 60,
    limit: int = 10,
) -> list[dict]:
    hits = []
    for curve in cache.curves:
        pts = curve.get("points") or []
        if not pts:
            continue
        d = min(distance(x, y, p[0], p[1]) for p in pts)
        if d <= radius:
            hits.append({**curve, "distance_pts": round(d, 1)})
    hits.sort(key=lambda h: h["distance_pts"])
    return hits[:limit]


def is_likely_grid_ref(span: dict, cache: ExtractionCache) -> bool:
    """
    Heuristic: 1-2 digit numbers inside small circles near grid lines.
    Spatial, not color-dependent.
    """
    text = span["text"].strip()
    if not re.match(r"^\d{1,2}$", text):
        return False
    cx, cy = span["cx"], span["cy"]
    nearby_curves = get_curves_near_point(cache, cx, cy, radius=25, limit=3)
    return len(nearby_curves) >= 2


def detect_scale_from_text(text: str) -> tuple[Optional[str], Optional[int]]:
    match = re.search(r"1\s*:\s*(\d+)", text)
    if not match:
        return None, None
    ratio = int(match.group(1))
    return f"1:{ratio}", ratio
