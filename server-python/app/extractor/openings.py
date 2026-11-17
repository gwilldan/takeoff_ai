"""
Doors and windows.

Two routes, because drawings mark openings in two ways.

Marked openings. Most plans tag every opening (Dr-02, D1, Wn-01, W3) beside
it. The tag says what it is; the wall says where and how wide. The nearest
axis-aligned room edge is the host wall, and the wall band is scanned along
that edge for the gap beside the tag: across a door the band shows almost no
ink, across a window only thin sill lines, both far less than a wall's full
thickness. Windows matter here — many offices draw the frame dark enough that
a window never shows up as a gap in the wall reading, only as a thinning.

Unmarked openings. Every bridge `walls.py` laid across a wall line is a
candidate, its long side the measured width. A bridge also closes gaps that
are not openings (a wall stopping short, a corridor end), so a candidate is
reported only with evidence, and the evidence says what it is:

- a window: two or more glazing lines running the full width of the gap;
- a door: its leaf and swing drawn beside it — a straight leaf standing
  open from either jamb, on either face, one leaf or two, the quarter
  circle it sweeps, and clear floor inside that circle. A round table or
  a chair has the circle but not the leaf.

A mark with no gap near it is still reported, centred on the mark at a
nominal width, with `confidence: low` so an assumed width is never mistaken
for a measured one. The vision model (`labelling.py`) then sees every
opening numbered and may correct an unmarked one's type or reject it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.extractor.grid import DrawingGrid, grid_ref_for_point
from app.extractor.raster import PageRaster
from app.extractor.walls import WallReading

# Dr-02, D1, D/3, DR 04, DOOR 2, Wn-01, W3, WIN-2, WINDOW 1, and a trailing letter (D1a).
OPENING_LABEL = re.compile(r"^(?P<kind>DOOR|DR|D|WINDOW|WIN|WN|W)\s*[-/.]?\s*(?P<number>\d{1,3})[A-Za-z]?$", re.IGNORECASE)
LAYERS = {"door": "doors", "window": "windows"}
DEFAULT_WIDTH_MM = {"door": 900.0, "window": 1200.0}

# Real-world sizes in millimetres.
MIN_WIDTH_MM = 400.0
MAX_WIDTH_MM = 3000.0
# A tag sits beside its opening: this far from the host edge at most, and the
# gap is searched this far along the edge either side of the tag.
MAX_TAG_TO_EDGE_MM = 1200.0
SEARCH_ALONG_MM = 1500.0
SCAN_STEP_PT = 0.5
# The band scanned across the wall, and the ink within it that still counts as
# wall: a wall is 150–300 mm of ink, a window's sill lines 30–60 mm, a door none.
BAND_MM = 300.0
WALL_DARK_MM = 100.0
# An unmarked opening closer than this to a marked one is the same opening.
SAME_OPENING_MM = 1000.0
# How far past the wall face to look for the room either side.
ROOM_PROBE_MM = 400.0

# A glazing line crosses at least this share of the gap.
GLAZING_COVER = 0.7
MIN_GLAZING_LINES = 2
# A swing arc is inked along at least ARC_COVER of its quarter circle, the
# open leaf along LEAF_COVER of its length, and the floor it sweeps (sampled
# at ARC_INSIDE of the leaf) is inked at most ARC_CLEAR.
ARC_COVER = 0.6
LEAF_COVER = 0.7
LEAF_SAMPLES = np.linspace(0.15, 0.9, 16)
ARC_INSIDE = 0.55
ARC_CLEAR = 0.25
ARC_DEGREES = np.radians(np.arange(15, 76, 5))
ARC_RADII = (0.85, 1.0, 1.1)  # the leaf is a little narrower or wider than the gap


@dataclass
class Opening:
    id: str
    kind: str
    polyline: list[list[float]]
    width_mm: float
    confidence: str
    notes: list[str] = field(default_factory=list)
    reference: str | None = None
    room_ids: list[str] = field(default_factory=list)
    exterior: bool | None = None
    grid_ref: str | None = None

    @property
    def centre(self) -> tuple[float, float]:
        (x1, y1), (x2, y2) = self.polyline
        return (x1 + x2) / 2, (y1 + y2) / 2

    def to_dict(self) -> dict:
        payload = {
            "id": self.id, "layer": LAYERS[self.kind], "type": self.kind, "reference": self.reference,
            "polyline": self.polyline, "widthMm": self.width_mm, "confidence": self.confidence,
            "notes": self.notes, "roomIds": self.room_ids or None, "exterior": self.exterior, "gridRef": self.grid_ref,
        }
        return {k: v for k, v in payload.items() if v is not None}


def opening_tags(text_spans: list[dict]) -> list[tuple[str, str, float, float]]:
    """(kind, reference, x, y) for every door/window mark on the page."""
    tags = []
    for span in text_spans:
        if match := OPENING_LABEL.match(span["text"]):
            kind = "door" if match["kind"].upper().startswith("D") else "window"
            tags.append((kind, span["text"].strip(), span["cx"], span["cy"]))
    return tags


def _rounded(points) -> list[list[float]]:
    return [[round(float(x), 2), round(float(y), 2)] for x, y in points]


class _Sides:
    """Which rooms lie either side of a wall, read from the pockets of the wall reading."""

    def __init__(self, reading: WallReading, rooms, raster: PageRaster):
        self.labels, self.raster = reading.labels, raster
        self.room_by_pocket = {room.pocket: room.id for room in rooms if room.pocket is not None}
        self.pockets = {p.label for p in reading.pockets}

    def __call__(self, centre_pt, normal) -> tuple[list[str], bool]:
        probe = self.raster.mm(BAND_MM / 2 + ROOM_PROBE_MM)
        found = []
        for sign in (-1, 1):
            x = int((centre_pt[0] + sign * probe * normal[0]) * self.raster.px_per_pt)
            y = int((centre_pt[1] + sign * probe * normal[1]) * self.raster.px_per_pt)
            inside = 0 <= y < self.labels.shape[0] and 0 <= x < self.labels.shape[1]
            found.append(int(self.labels[y, x]) if inside else -1)
        rooms = sorted({self.room_by_pocket[p] for p in found if p in self.room_by_pocket})
        return rooms, any(p not in self.pockets for p in found)


# --- marked openings: scan the host wall beside the tag -----------------------

def _axis_edges(rooms) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    edges = []
    for room in rooms:
        if room.confidence == "low":
            continue
        for a, b in zip(room.polyline, room.polyline[1:] + room.polyline[:1]):
            if (a[0] == b[0]) != (a[1] == b[1]):
                edges.append(((a[0], a[1]), (b[0], b[1])))
    return edges


def _nearest_edge(point: tuple[float, float], edges):
    """Closest edge to a point as (a, unit, length, distance, offset along edge)."""
    best = None
    for a, b in edges:
        length = math.dist(a, b)
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        along = max(0.0, min(length, (point[0] - a[0]) * ux + (point[1] - a[1]) * uy))
        distance = math.dist(point, (a[0] + ux * along, a[1] + uy * along))
        if best is None or distance < best[3]:
            best = (a, (ux, uy), length, distance, along)
    return best


class _WallScanner:
    """Ink thickness across a wall band, sampled at the raster's resolution."""

    def __init__(self, raster: PageRaster):
        self.barrier = raster.barrier
        self.px = raster.px_per_pt
        self.wall_dark_pt = raster.mm(WALL_DARK_MM)
        band = raster.mm(BAND_MM)
        self.offsets = np.arange(-band, band, 1 / self.px)

    def dark_pts(self, x: float, y: float, normal: tuple[float, float]) -> float:
        xs = ((x + normal[0] * self.offsets) * self.px).astype(int)
        ys = ((y + normal[1] * self.offsets) * self.px).astype(int)
        inside = (xs >= 0) & (xs < self.barrier.shape[1]) & (ys >= 0) & (ys < self.barrier.shape[0])
        return float(self.barrier[ys[inside], xs[inside]].sum()) / self.px

    def gap(self, a, unit, length, start) -> tuple[float, float] | None:
        """Extent along the edge of the non-wall run containing `start`, or None."""
        normal = (-unit[1], unit[0])

        def is_wall(t: float) -> bool:
            return self.dark_pts(a[0] + unit[0] * t, a[1] + unit[1] * t, normal) >= self.wall_dark_pt

        if is_wall(start):
            return None
        lo = hi = start
        while lo - SCAN_STEP_PT >= 0 and not is_wall(lo - SCAN_STEP_PT):
            lo -= SCAN_STEP_PT
        while hi + SCAN_STEP_PT <= length and not is_wall(hi + SCAN_STEP_PT):
            hi += SCAN_STEP_PT
        return lo, hi


def _marked(tags, rooms, raster: PageRaster, sides: _Sides, grid: DrawingGrid) -> list[Opening]:
    edges = _axis_edges(rooms)
    scanner = _WallScanner(raster)
    mm_per_pt = 1 / raster.pt_per_mm
    reach = raster.mm(SEARCH_ALONG_MM)
    openings = []

    for kind, reference, x, y in tags:
        host = _nearest_edge((x, y), edges) if edges else None
        common = dict(id="", kind=kind, reference=reference, grid_ref=grid_ref_for_point(grid, x, y))
        half = DEFAULT_WIDTH_MM[kind] / 2 / mm_per_pt

        if host is None or host[3] > raster.mm(MAX_TAG_TO_EDGE_MM):
            openings.append(Opening(polyline=_rounded([(x - half, y), (x + half, y)]), width_mm=DEFAULT_WIDTH_MM[kind],
                                    confidence="low", notes=["No wall found near the mark; placed at the mark with an assumed width"], **common))
            continue

        a, unit, length, _, along = host
        span = None
        for offset in sorted(np.arange(-reach, reach + SCAN_STEP_PT, 1.0), key=abs):
            if 0 <= along + offset <= length and (gap := scanner.gap(a, unit, length, along + offset)):
                if MIN_WIDTH_MM <= (gap[1] - gap[0]) * mm_per_pt <= MAX_WIDTH_MM:
                    span = gap
                    break

        if span is None:
            lo, hi = max(0.0, along - half), min(length, along + half)
            width_mm, confidence = DEFAULT_WIDTH_MM[kind], "low"
            notes = ["No gap found in the wall beside the mark; width is an assumed default"]
        else:
            lo, hi = span
            width_mm, confidence = (hi - lo) * mm_per_pt, "high"
            notes = [f"Width measured from the gap in the wall beside the mark {reference}"]

        ends = [(a[0] + unit[0] * t, a[1] + unit[1] * t) for t in (lo, hi)]
        centre = ((ends[0][0] + ends[1][0]) / 2, (ends[0][1] + ends[1][1]) / 2)
        room_ids, exterior = sides(centre, (-unit[1], unit[0]))
        openings.append(Opening(polyline=_rounded(ends), width_mm=float(round(width_mm)), confidence=confidence,
                                notes=notes, room_ids=room_ids, exterior=exterior, **common))
    return openings


# --- unmarked openings: bridges with evidence ---------------------------------

def _glazing_lines(barrier: np.ndarray, middle: np.ndarray, along: np.ndarray, across: np.ndarray, width_px: float, depth_px: float) -> int:
    """Distinct lines inside the gap that run (nearly) its full width."""
    rows = np.arange(-depth_px / 2, depth_px / 2 + 1)
    cols = np.linspace(-width_px / 2 * 0.9, width_px / 2 * 0.9, max(8, int(width_px)))
    xs = (middle[0] + rows[:, None] * across[0] + cols[None, :] * along[0]).astype(int).clip(0, barrier.shape[1] - 1)
    ys = (middle[1] + rows[:, None] * across[1] + cols[None, :] * along[1]).astype(int).clip(0, barrier.shape[0] - 1)
    covered = barrier[ys, xs].mean(axis=1) >= GLAZING_COVER
    return int(np.count_nonzero(covered[1:] & ~covered[:-1]) + covered[0])


def _swing_arc(ink: np.ndarray, middle: np.ndarray, along: np.ndarray, across: np.ndarray, width_px: float, depth_px: float) -> bool:
    """A door leaf and its swing drawn beside the gap, with clear floor inside it."""
    def inked(points: np.ndarray) -> float:
        xs = points[:, 0].astype(int).clip(0, ink.shape[1] - 1)
        ys = points[:, 1].astype(int).clip(0, ink.shape[0] - 1)
        return float(ink[ys, xs].mean())

    for leaf in (width_px, width_px / 2):
        for jamb in (-1, 1):
            for face in (across, -across):
                hinge = middle + jamb * along * width_px / 2 + face * depth_px / 2
                quarter = np.cos(ARC_DEGREES)[:, None] * -jamb * along + np.sin(ARC_DEGREES)[:, None] * face
                if inked(hinge + leaf * ARC_INSIDE * quarter) > ARC_CLEAR:
                    continue
                if inked(hinge + leaf * LEAF_SAMPLES[:, None] * face) < LEAF_COVER:
                    continue
                if any(inked(hinge + leaf * radius * quarter) >= ARC_COVER for radius in ARC_RADII):
                    return True
    return False


def _unmarked(reading: WallReading, raster: PageRaster, sides: _Sides, grid: DrawingGrid) -> list[Opening]:
    ink = cv2.dilate(raster.ink, np.ones((3, 3), np.uint8))
    mm_per_px = 1 / raster.px(1)
    openings = []

    count, labels, stats, _ = cv2.connectedComponentsWithStats(reading.bridges, connectivity=8)
    for label in range(1, count):
        x, y, w, h, _ = stats[label]
        ys, xs = np.nonzero(labels[y:y + h, x:x + w] == label)
        (cx, cy), (rw, rh), angle = cv2.minAreaRect(np.column_stack([xs + x, ys + y]).astype(np.float32))
        width_px, depth_px = max(rw, rh), min(rw, rh)
        if not MIN_WIDTH_MM <= width_px * mm_per_px <= MAX_WIDTH_MM:
            continue
        theta = math.radians(angle if rw >= rh else angle + 90)
        along, across = np.array([math.cos(theta), math.sin(theta)]), np.array([-math.sin(theta), math.cos(theta)])
        middle = np.array([cx, cy])

        window = _glazing_lines(raster.barrier, middle, along, across, width_px, depth_px) >= MIN_GLAZING_LINES
        if not window and not _swing_arc(ink, middle, along, across, width_px, depth_px):
            continue
        ends = [(middle - along * width_px / 2) / raster.px_per_pt, (middle + along * width_px / 2) / raster.px_per_pt]
        centre = (cx / raster.px_per_pt, cy / raster.px_per_pt)
        room_ids, exterior = sides(centre, across)
        openings.append(Opening(
            id="", kind="window" if window else "door", polyline=_rounded(ends),
            width_mm=float(round(width_px * mm_per_px)), confidence="medium",
            notes=["Unmarked; width measured from the gap in the wall, " + ("glazing lines drawn across it" if window else "a door swing drawn beside it")],
            room_ids=room_ids, exterior=exterior, grid_ref=grid_ref_for_point(grid, *centre),
        ))
    return openings


def detect_openings(cache, raster: PageRaster, reading: WallReading, rooms, grid: DrawingGrid) -> list[Opening]:
    sides = _Sides(reading, rooms, raster)
    marked = _marked(opening_tags(cache.text_spans), rooms, raster, sides, grid)
    same = raster.mm(SAME_OPENING_MM)
    unmarked = [o for o in _unmarked(reading, raster, sides, grid)
                if all(math.dist(o.centre, m.centre) > same for m in marked)]
    return number_openings(marked + unmarked)


def number_openings(openings: list[Opening]) -> list[Opening]:
    counters = {"door": 0, "window": 0}
    for opening in openings:
        counters[opening.kind] += 1
        opening.id = f"{opening.kind}-{counters[opening.kind]:03d}"
    return openings
