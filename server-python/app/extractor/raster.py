"""
Pixel-space room detection.

Walls on a drawing may be vectors, hatching, or — as on scanned and
image-backed sheets — nothing but pixels. Rendering the page and working in
pixel space handles all three the same way: dark pixels are barriers, a room
is the pocket of free space around its tag, and its outline is the contour of
that pocket.

Two things are erased from the barrier mask before any fill, because the
cache knows exactly where they are: text (so a label never splits or seals a
room) and hairline vectors (dimension lines, grid lines and leaders, which run
straight through rooms). Door openings are bridged by morphological closing,
with the radius chosen per room against the printed area in `rooms.py`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.extractor.render import render_page_array

PX_PER_PT = 3.0

# A pixel is a barrier when every channel is below this: black and dark-red
# walls count, bright red dimension lines and grey ghosted layers do not.
DARK_MAX_CHANNEL = 150

# Vector strokes at or below this width are never walls; they are erased.
HAIRLINE_PT = 0.5

# Padding added around each text span when erasing it, in points.
TEXT_PAD_PT = 1.0

# How far from the tag to look for free space when the tag sits on a pixel.
SEED_SEARCH_PT = 6.0

# Opening/closing kernel applied to a found room: removes door-leaf slivers and
# pokes into doorways, narrower than any real room.
CLEANUP_PT = 8.0

# Contour simplification tolerance and the angle within which an edge is
# treated as horizontal or vertical.
SIMPLIFY_PT = 2.0
AXIS_TOLERANCE_DEG = 12.0
MIN_EDGE_PT = 3.0
# A diagonal shorter than this is a stair-step from a door swing, not a splayed wall.
MIN_DIAGONAL_EDGE_PT = 15.0


def _kernel(size_pt: float, px_per_pt: float) -> np.ndarray:
    size = int(size_pt * px_per_pt) | 1
    return cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))


@dataclass
class PageRaster:
    px_per_pt: float
    barrier: np.ndarray  # uint8, 1 where a wall (or anything dark) is
    _closed: dict[float, np.ndarray] = field(default_factory=dict, repr=False)

    @classmethod
    def from_cache(cls, cache, px_per_pt: float = PX_PER_PT) -> "PageRaster":
        image = render_page_array(cache.pdf_path, cache.page_index, px_per_pt)
        barrier = (image.max(axis=2) < DARK_MAX_CHANNEL).astype(np.uint8)

        def px(value: float) -> int:
            return int(round(value * px_per_pt))

        for span in cache.text_spans:
            y0, y1 = px(span["y"] - TEXT_PAD_PT), px(span["y2"] + TEXT_PAD_PT)
            x0, x1 = px(span["x"] - TEXT_PAD_PT), px(span["x2"] + TEXT_PAD_PT)
            barrier[max(0, y0):y1, max(0, x0):x1] = 0

        for line in cache.line_segments:
            if (line["stroke_width"] or 0) <= HAIRLINE_PT:
                start = (px(line["start"][0]), px(line["start"][1]))
                end = (px(line["end"][0]), px(line["end"][1]))
                cv2.line(barrier, start, end, 0, 3)

        return cls(px_per_pt=px_per_pt, barrier=barrier)

    def closed(self, radius_pt: float) -> np.ndarray:
        """Barrier with gaps narrower than 2×radius bridged (door openings)."""
        if radius_pt not in self._closed:
            self._closed[radius_pt] = (
                self.barrier if radius_pt == 0
                else cv2.morphologyEx(self.barrier, cv2.MORPH_CLOSE, _kernel(2 * radius_pt, self.px_per_pt))
            )
        return self._closed[radius_pt]

    def region_around(self, seed_pt: tuple[float, float], radius_pt: float) -> np.ndarray | None:
        """Flood fill the free space around a point; None when the point is walled in."""
        free = 1 - self.closed(radius_pt)
        sx, sy = int(seed_pt[0] * self.px_per_pt), int(seed_pt[1] * self.px_per_pt)
        reach = int(SEED_SEARCH_PT * self.px_per_pt)

        if not (0 <= sy < free.shape[0] and 0 <= sx < free.shape[1]):
            return None
        if free[sy, sx] == 0:
            window = free[max(0, sy - reach):sy + reach + 1, max(0, sx - reach):sx + reach + 1]
            ys, xs = np.nonzero(window)
            if len(xs) == 0:
                return None
            oy, ox = max(0, sy - reach), max(0, sx - reach)
            nearest = np.argmin((xs + ox - sx) ** 2 + (ys + oy - sy) ** 2)
            sx, sy = xs[nearest] + ox, ys[nearest] + oy

        mask = np.zeros((free.shape[0] + 2, free.shape[1] + 2), np.uint8)
        cv2.floodFill(free.copy(), mask, (sx, sy), 2, flags=4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY)
        return mask[1:-1, 1:-1]

    def area_pts2(self, region: np.ndarray) -> float:
        return float(np.count_nonzero(region)) / (self.px_per_pt ** 2)

    def polygon(self, region: np.ndarray) -> list[list[float]]:
        """Outline of a region as an orthogonalised polygon in PDF points."""
        kernel = _kernel(CLEANUP_PT, self.px_per_pt)
        cleaned = cv2.morphologyEx(cv2.morphologyEx(region, cv2.MORPH_OPEN, kernel), cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return []
        contour = max(contours, key=cv2.contourArea)
        points = cv2.approxPolyDP(contour, SIMPLIFY_PT * self.px_per_pt, True).reshape(-1, 2) / self.px_per_pt
        return orthogonalize(points.tolist())


def _edge_class(a: list[float], b: list[float]) -> str:
    angle = abs(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))) % 180
    if min(angle, 180 - angle) <= AXIS_TOLERANCE_DEG:
        return "h"
    return "v" if abs(angle - 90) <= AXIS_TOLERANCE_DEG else "d"


def orthogonalize(points: list[list[float]]) -> list[list[float]]:
    """
    Square up a traced outline.

    Short edges go first (diagonal stair-steps left by door swings are allowed
    to be longer than axis-aligned ones before they count as real), then runs
    of same-direction edges collapse to one, then every horizontal edge gets a
    single y and every vertical edge a single x. A corner takes its y from the
    horizontal side and its x from the vertical side, so nothing fights. Long
    diagonals are left alone: a splayed room keeps its splay.
    """
    pts = [list(p) for p in points]

    def drop_short() -> bool:
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            limit = MIN_EDGE_PT if _edge_class(a, b) != "d" else MIN_DIAGONAL_EDGE_PT
            if math.dist(a, b) < limit:
                pts[i] = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
                del pts[(i + 1) % len(pts)]
                return True
        return False

    def drop_collinear() -> bool:
        for i in range(len(pts)):
            before, after = _edge_class(pts[i - 1], pts[i]), _edge_class(pts[i], pts[(i + 1) % len(pts)])
            if before == after and before != "d":
                del pts[i]
                return True
        return False

    while len(pts) > 3 and (drop_short() or drop_collinear()):
        pass

    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        kind = _edge_class(a, b)
        if kind == "h":
            a[1] = b[1] = (a[1] + b[1]) / 2
        elif kind == "v":
            a[0] = b[0] = (a[0] + b[0]) / 2

    return [[round(p[0], 2), round(p[1], 2)] for p in pts]


def polygon_area_pts2(points: list[list[float]]) -> float:
    return abs(sum(
        points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    )) / 2
