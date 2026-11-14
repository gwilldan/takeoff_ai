"""
Spatial helpers — bbox checks, proximity, polygon area.
"""

from __future__ import annotations

import math
import re
from typing import Optional


def point_in_bbox(x: float, y: float, bbox: list[float]) -> bool:
    x0, y0, x1, y1 = bbox
    return x0 <= x <= x1 and y0 <= y <= y1


def bbox_from_center(cx: float, cy: float, radius: float) -> list[float]:
    return [cx - radius, cy - radius, cx + radius, cy + radius]


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def point_to_segment_distance(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float,
) -> float:
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return distance(px, py, x1, y1)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return distance(px, py, proj_x, proj_y)


def colors_close(a: list[float] | None, b: list[float] | None, tol: float = 0.15) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if len(a) < 3 or len(b) < 3:
        return False
    return all(abs(a[i] - b[i]) <= tol for i in range(3))


def parse_color_key(key: str) -> Optional[list[float]]:
    """Parse '[0.0, 0.0, 0.0]' style color key from cache summary."""
    match = re.match(r"\[([\d.\-eE]+),\s*([\d.\-eE]+),\s*([\d.\-eE]+)\]", key)
    if not match:
        return None
    return [float(match.group(i)) for i in range(1, 4)]


def polygon_area_pts(points: list[list[float]]) -> float:
    """Shoelace formula — area in PDF points²."""
    if len(points) < 3:
        return 0.0
    area = 0.0
    n = len(points)
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
    return abs(area) / 2.0


def pts2_to_m2(area_pts2: float, scale_ratio: int) -> float:
    """Convert PDF points² to real-world m² at the given scale."""
    PDF_POINTS_PER_INCH = 72
    MM_PER_INCH = 25.4
    mm_per_pt = (MM_PER_INCH / PDF_POINTS_PER_INCH) * scale_ratio
    mm2 = area_pts2 * (mm_per_pt ** 2)
    return round(mm2 / 1_000_000, 2)
