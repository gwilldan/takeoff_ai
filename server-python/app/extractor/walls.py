"""
Walls, read from the drawing itself: no labels needed.

A wall on a plan is a band of ink 80–600 mm thick that runs straight for a
while. Offices draw that band in different ways: solid black, hatched between
two lines, or as two hollow lines. Furniture, dimension lines, door swings
and floor patterns are thinner, shorter, curved or fatter. So walls are read
in four steps, each in real millimetres through the sheet scale:

1. Close the ink so a hatched or hollow wall becomes a solid band.
2. Open it so anything thinner than a wall disappears.
3. Drop anything fatter than a wall (stairs, hatched floor zones, desks).
4. Keep only ink that forms a straight run in one of four directions, so
   chairs, door swings and fixtures disappear.

How far to close depends on the office: hatching is a few hundred millimetres
apart, a hollow wall needs more. A vector PDF may also mark its cut walls
directly (`cache.cut_regions`). Rather than guess, every interpretation is
built, sealed and scored, and the one under which the drawing resolves into
the most plausible rooms wins. Printed room areas, where the drawing has
them, weigh heavily in that score.

Sealing: a door or window is a gap in a wall line. For every free pixel the
nearest wall to each side along its row (and column) is measured; when the
gap is short and at least one side is a run of the same-direction wall, the
pixel is bridged. Collinear walls may be bridged further apart than a wall
end and a cross wall. Bridges are the openings `openings.py` reports, and
rooms are the free pockets left inside the sealed walls.

This module produces a single wall mask. A trained segmentation model could
replace `_interpretations` later without changing anything downstream.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.extractor.raster import PageRaster

LAYER = "walls"

# Real-world sizes in millimetres.
MIN_THICKNESS_MM = 80.0
MAX_THICKNESS_MM = 600.0
FILL_MM = (120.0, 260.0)  # hatch spacing / hollow wall interior bridged by closing
MIN_RUN_MM = 600.0  # straight run a wall must make
AXIS_RUN_MM = 700.0  # run that marks a wall as horizontal or vertical when sealing (> thickest wall)
SAME_LINE_GAP_MM = 3200.0  # widest opening between two pieces of one wall line
CROSS_GAP_MM = 1600.0  # widest opening between a wall end and a cross wall
MIN_SEGMENT_MM = 300.0
SEAL_STRIP_ROWS = 512  # rows sealed at a time; bounds memory on large sheets
# Ink fatter than a wall and at least this big is a solid — a stair core, a
# hatched void, a shaft. It bounds rooms but is not reported as wall.
MIN_SOLID_M2 = 4.0

# A pocket is a plausible room between these sizes, and when it fills at
# least this share of its bounding box (a sliver between hatch strokes does not).
MIN_ROOM_M2 = 1.5
MAX_ROOM_M2 = 400.0
MIN_BOX_FILL = 0.3

# Scoring: a room counts up to this many square metres (so one merged hall
# does not outscore the rooms it swallowed); a printed area matched within
# tolerance is worth far more.
ROOM_SCORE_CAP_M2 = 40.0
MATCHED_TAG_SCORE = 30.0
TAG_TOLERANCE = 0.15


def _disk(pixels: float) -> np.ndarray:
    size = max(3, int(round(pixels)) | 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def _line_kernels(pixels: float) -> list[np.ndarray]:
    n = max(3, int(pixels) | 1)
    diagonal = np.eye(n, dtype=np.uint8)
    return [np.ones((1, n), np.uint8), np.ones((n, 1), np.uint8), diagonal, np.fliplr(diagonal).copy()]


def _wall_shaped(mask: np.ndarray, raster: PageRaster) -> tuple[np.ndarray, np.ndarray]:
    """Steps 2–4: (bands of wall thickness that run straight, large solids)."""
    thin = _disk(raster.px(MIN_THICKNESS_MM))
    walls = cv2.morphologyEx(mask, cv2.MORPH_OPEN, thin)
    fat = cv2.morphologyEx(walls, cv2.MORPH_OPEN, _disk(raster.px(MAX_THICKNESS_MM)))
    walls &= 1 - cv2.dilate(fat, thin)
    walls = cv2.morphologyEx(walls, cv2.MORPH_OPEN, thin)
    straight = np.zeros_like(walls)
    for kernel in _line_kernels(raster.px(MIN_RUN_MM)):
        straight |= cv2.morphologyEx(walls, cv2.MORPH_OPEN, kernel)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(fat, connectivity=8)
    large = np.zeros(count, np.uint8)
    large[1:] = [raster.m2(a) >= MIN_SOLID_M2 for a in stats[1:, cv2.CC_STAT_AREA]]
    return straight, large[labels]


def _cut_mask(cache, raster: PageRaster) -> np.ndarray | None:
    if not cache.cut_regions:
        return None
    mask = np.zeros_like(raster.ink)
    for region in cache.cut_regions:
        cv2.fillPoly(mask, [np.round(np.array(region) * raster.px_per_pt).astype(np.int32)], 1)
    return mask


def _interpretations(cache, raster: PageRaster):
    """(name, (walls, solids)) for every reading of the walls worth scoring."""
    closed = {fill: cv2.morphologyEx(raster.ink, cv2.MORPH_CLOSE, _disk(raster.px(fill))) for fill in FILL_MM}
    for fill, mask in closed.items():
        yield f"ink closed at {fill:.0f} mm", _wall_shaped(mask, raster)
    cut = _cut_mask(cache, raster)
    if cut is not None and cut.any():
        yield "vector cut regions", _wall_shaped(cut, raster)
        yield "vector cut regions + ink", _wall_shaped(cut | closed[FILL_MM[0]], raster)


def _gap_to(mask: np.ndarray, reverse: bool) -> np.ndarray:
    """Per row, the distance in pixels to the nearest set pixel on one side."""
    width = mask.shape[1]
    columns = np.arange(width, dtype=np.int32)
    source = mask[:, ::-1] if reverse else mask
    last = np.maximum.accumulate(np.where(source > 0, columns, -width * 4), axis=1)
    distance = columns - last
    return distance[:, ::-1] if reverse else distance


def _bridge_rows(barrier: np.ndarray, runs: np.ndarray, same_line: float, cross: float) -> np.ndarray:
    """Bridges along rows. Rows are independent, so they are done in strips to bound memory."""
    bridges = np.zeros_like(barrier)
    for top in range(0, barrier.shape[0], SEAL_STRIP_ROWS):
        rows = slice(top, top + SEAL_STRIP_ROWS)
        wall, run = barrier[rows], runs[rows]
        left, right = _gap_to(wall, False), _gap_to(wall, True)
        left_run, right_run = _gap_to(run, False) <= left + 2, _gap_to(run, True) <= right + 2
        gap = left + right
        bridges[rows] = (wall == 0) & (((left_run & right_run) & (gap <= same_line)) | ((left_run | right_run) & (gap <= cross)))
    return bridges


def seal(walls: np.ndarray, solids: np.ndarray, raster: PageRaster) -> np.ndarray:
    """Bridges across door and window gaps, as a mask. A gap may end on a solid."""
    run = _line_kernels(raster.px(AXIS_RUN_MM))
    same_line, cross = raster.px(SAME_LINE_GAP_MM), raster.px(CROSS_GAP_MM)
    barrier = walls | solids
    horizontal = _bridge_rows(barrier, cv2.morphologyEx(walls, cv2.MORPH_OPEN, run[0]), same_line, cross)
    vertical = _bridge_rows(barrier.T, cv2.morphologyEx(walls, cv2.MORPH_OPEN, run[1]).T, same_line, cross).T
    return cv2.morphologyEx(horizontal | vertical, cv2.MORPH_OPEN, _disk(raster.px(MIN_THICKNESS_MM) * 0.75))


@dataclass
class Pocket:
    label: int
    box: tuple[int, int, int, int]  # x, y, width, height in pixels
    area_m2: float


@dataclass
class WallReading:
    name: str
    walls: np.ndarray
    solids: np.ndarray
    bridges: np.ndarray
    labels: np.ndarray  # free-space component per pixel
    pockets: list[Pocket]
    score: float

    def pocket_at(self, raster: PageRaster, x_pt: float, y_pt: float) -> Pocket | None:
        x, y = int(x_pt * raster.px_per_pt), int(y_pt * raster.px_per_pt)
        if not (0 <= y < self.labels.shape[0] and 0 <= x < self.labels.shape[1]):
            return None
        label = self.labels[y, x]
        return next((p for p in self.pockets if p.label == label), None)


def _pockets(barrier: np.ndarray, raster: PageRaster) -> tuple[np.ndarray, list[Pocket]]:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(1 - barrier, connectivity=4)
    height, width = barrier.shape
    pockets = []
    for label in range(1, count):
        x, y, w, h, pixels = (int(v) for v in stats[label])
        area = raster.m2(pixels)
        touches_edge = x == 0 or y == 0 or x + w >= width or y + h >= height
        if not touches_edge and MIN_ROOM_M2 <= area <= MAX_ROOM_M2 and pixels >= MIN_BOX_FILL * w * h:
            pockets.append(Pocket(label, (x, y, w, h), area))
    return labels, pockets


def _score(pockets: list[Pocket], labels: np.ndarray, raster: PageRaster, tags: list[tuple[float, float, float | None]]) -> float:
    score = sum(min(p.area_m2, ROOM_SCORE_CAP_M2) for p in pockets)
    by_label = {p.label: p for p in pockets}
    for x, y, printed in tags:
        px, py = int(x * raster.px_per_pt), int(y * raster.px_per_pt)
        pocket = by_label.get(int(labels[py, px])) if 0 <= py < labels.shape[0] and 0 <= px < labels.shape[1] else None
        if pocket and printed and abs(pocket.area_m2 - printed) <= TAG_TOLERANCE * printed:
            score += MATCHED_TAG_SCORE
    return score


def read_walls(cache, raster: PageRaster, tags: list[tuple[float, float, float | None]], region: np.ndarray | None = None) -> WallReading:
    """
    The best reading of the page's walls. `tags` are room labels as
    (x, y, printed area or None) in points; `region` limits the reading to
    the drawing's plan viewports.
    """
    best: WallReading | None = None
    for name, (walls, found_solids) in _interpretations(cache, raster):
        if region is not None:
            walls &= region
            found_solids &= region
        # A large hatched area is a stair core or void to one office and a
        # tiled floor to another, so each reading is scored both ways.
        for solids, label in ((found_solids, f"{name}, hatched areas solid"), (np.zeros_like(walls), name)):
            bridges = seal(walls, solids, raster)
            labels, pockets = _pockets(walls | solids | bridges, raster)
            score = _score(pockets, labels, raster, tags)
            if best is None or score > best.score:
                best = WallReading(label, walls, solids, bridges, labels, pockets, score)
            if not solids.any():
                break  # no solids found: the two variants are the same
    return best


@dataclass
class Wall:
    id: str
    polyline: list[list[float]]
    length_mm: float
    thickness_mm: float

    def to_dict(self) -> dict:
        return {
            "id": self.id, "layer": LAYER, "type": "wall", "polyline": self.polyline,
            "lengthMm": self.length_mm, "thicknessMm": self.thickness_mm,
            "confidence": "medium", "notes": ["Wall band read from the drawing"],
        }


def wall_segments(reading: WallReading, raster: PageRaster) -> list[Wall]:
    """
    The wall mask as straight segments: horizontal and vertical runs first,
    whatever is left (splayed walls) after. Each is reported as its rotated
    rectangle, so length and thickness are both measurable.
    """
    walls = reading.walls
    horizontal = cv2.morphologyEx(walls, cv2.MORPH_OPEN, _line_kernels(raster.px(MIN_SEGMENT_MM))[0])
    vertical = cv2.morphologyEx(walls, cv2.MORPH_OPEN, _line_kernels(raster.px(MIN_SEGMENT_MM))[1])
    vertical &= 1 - horizontal
    rest = walls & (1 - cv2.dilate(horizontal | vertical, _disk(raster.px(MIN_THICKNESS_MM))))

    segments: list[Wall] = []
    mm_per_px = 1 / raster.px(1)
    for mask in (horizontal, vertical, rest):
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for label in range(1, count):
            if stats[label, cv2.CC_STAT_AREA] < raster.px(MIN_SEGMENT_MM) * raster.px(MIN_THICKNESS_MM):
                continue
            ys, xs = np.nonzero(labels[stats[label, 1]:stats[label, 1] + stats[label, 3],
                                       stats[label, 0]:stats[label, 0] + stats[label, 2]] == label)
            points = np.column_stack([xs + stats[label, 0], ys + stats[label, 1]]).astype(np.float32)
            (cx, cy), (w, h), angle = cv2.minAreaRect(points)
            length, thickness = max(w, h) * mm_per_px, min(w, h) * mm_per_px
            if length < MIN_SEGMENT_MM or not MIN_THICKNESS_MM * 0.75 <= thickness <= MAX_THICKNESS_MM:
                continue
            corners = cv2.boxPoints(((cx, cy), (w + 1, h + 1), angle)) / raster.px_per_pt
            segments.append(Wall(
                id=f"wall-{len(segments) + 1:03d}",
                polyline=[[round(float(x), 2), round(float(y), 2)] for x, y in corners],
                length_mm=float(round(length)), thickness_mm=float(round(thickness)),
            ))
    return segments
