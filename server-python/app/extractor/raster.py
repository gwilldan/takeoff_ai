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

MM_PER_PT = 25.4 / 72

# Render resolution, capped so an A0 sheet does not become a 70-megapixel mask.
PX_PER_PT = 3.0
MAX_PIXELS = 30_000_000

# A pixel is a barrier when every channel is below this: black and dark-red
# walls count, bright red dimension lines and grey ghosted layers do not.
DARK_MAX_CHANNEL = 150

# Vector strokes at or below this width are dimension lines, grid lines and
# leaders — but only on sheets that also draw something heavier. A CAD export
# drawn entirely in hairlines keeps them, because then the walls are hairlines
# too, and the colour threshold has to do the work alone.
HAIRLINE_PT = 0.5
HEAVY_STROKE_RATIO = 1.5

# Padding added around each text span when erasing it, in points.
TEXT_PAD_PT = 1.0

# How far from the tag to look for free space when the tag sits on a pixel.
SEED_SEARCH_PT = 6.0

# Real-world sizes, converted through the drawing scale so a 1:50 and a 1:200
# sheet get the same treatment.
#
# Opening/closing kernel applied to a found room: removes door-leaf slivers and
# pokes into doorways. Must stay narrower than any real room or corridor.
CLEANUP_MM = 300.0
# Edges shorter than these are traced noise. A diagonal has to be longer than a
# door swing's radius before it counts as a real splayed wall.
MIN_EDGE_MM = 100.0
MIN_DIAGONAL_EDGE_MM = 1000.0

# Contour simplification tolerance (a raster artefact, so in points) and the
# angle within which an edge is treated as horizontal or vertical.
SIMPLIFY_PT = 2.0
AXIS_TOLERANCE_DEG = 12.0


def _kernel(size_pt: float, px_per_pt: float) -> np.ndarray:
    size = int(size_pt * px_per_pt) | 1
    return cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))


def hairlines_are_noise(cache) -> bool:
    """True when hairline vectors can be erased: the walls are drawn some other way."""
    widths = sorted(line["stroke_width"] or 0 for line in cache.line_segments)
    heaviest_common = widths[int(len(widths) * 0.9)] if widths else 0
    return cache.image_coverage >= 0.2 or heaviest_common > HAIRLINE_PT * HEAVY_STROKE_RATIO


@dataclass
class PageRaster:
    px_per_pt: float
    pt_per_mm: float
    barrier: np.ndarray  # uint8, 1 where a wall (or anything dark) is
    _closed: dict[float, np.ndarray] = field(default_factory=dict, repr=False)

    @classmethod
    def from_cache(cls, cache, scale_ratio: float) -> "PageRaster":
        px_per_pt = min(PX_PER_PT, math.sqrt(MAX_PIXELS / (cache.page_width_pts * cache.page_height_pts)))
        image = render_page_array(cache.pdf_path, cache.page_index, px_per_pt)
        barrier = (image.max(axis=2) < DARK_MAX_CHANNEL).astype(np.uint8)

        def px(value: float) -> int:
            return int(round(value * px_per_pt))

        for span in cache.text_spans:
            y0, y1 = px(span["y"] - TEXT_PAD_PT), px(span["y2"] + TEXT_PAD_PT)
            x0, x1 = px(span["x"] - TEXT_PAD_PT), px(span["x2"] + TEXT_PAD_PT)
            barrier[max(0, y0):y1, max(0, x0):x1] = 0

        if hairlines_are_noise(cache):
            for line in cache.line_segments:
                if (line["stroke_width"] or 0) <= HAIRLINE_PT:
                    start = (px(line["start"][0]), px(line["start"][1]))
                    end = (px(line["end"][0]), px(line["end"][1]))
                    cv2.line(barrier, start, end, 0, 3)

        return cls(px_per_pt=px_per_pt, pt_per_mm=1 / (MM_PER_PT * scale_ratio), barrier=barrier)

    def mm(self, value_mm: float) -> float:
        """Real-world millimetres to points on this sheet."""
        return value_mm * self.pt_per_mm

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
        kernel = _kernel(self.mm(CLEANUP_MM), self.px_per_pt)
        cleaned = cv2.morphologyEx(cv2.morphologyEx(region, cv2.MORPH_OPEN, kernel), cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return []
        contour = max(contours, key=cv2.contourArea)
        points = cv2.approxPolyDP(contour, SIMPLIFY_PT * self.px_per_pt, True).reshape(-1, 2) / self.px_per_pt
        return orthogonalize(points.tolist(), self.mm(MIN_EDGE_MM), self.mm(MIN_DIAGONAL_EDGE_MM))


def _edge_class(a: list[float], b: list[float]) -> str:
    angle = abs(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))) % 180
    if min(angle, 180 - angle) <= AXIS_TOLERANCE_DEG:
        return "h"
    return "v" if abs(angle - 90) <= AXIS_TOLERANCE_DEG else "d"


def orthogonalize(points: list[list[float]], min_edge: float = 3.0, min_diagonal: float = 28.0) -> list[list[float]]:
    """
    Square up a traced outline. `min_edge` and `min_diagonal` are in points.

    Short edges go first (a diagonal has to be longer than a door swing's
    radius before it counts as a real splayed wall), then runs
    of same-direction edges collapse to one, then every horizontal edge gets a
    single y and every vertical edge a single x. A corner takes its y from the
    horizontal side and its x from the vertical side, so nothing fights. Long
    diagonals are left alone: a splayed room keeps its splay.
    """
    pts = [list(p) for p in points]

    def drop_short() -> bool:
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            limit = min_edge if _edge_class(a, b) != "d" else min_diagonal
            if math.dist(a, b) >= limit:
                continue
            before, after = _edge_class(pts[i - 1], a), _edge_class(b, pts[(i + 2) % n])
            if {before, after} == {"h", "v"}:
                # A jog between a horizontal and a vertical run is a cut corner:
                # extend both runs to where they meet instead of averaging.
                pts[i] = [b[0], a[1]] if before == "h" else [a[0], b[1]]
            else:
                pts[i] = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
            del pts[(i + 1) % n]
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
