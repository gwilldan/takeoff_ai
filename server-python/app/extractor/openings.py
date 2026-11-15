"""
Doors and windows, anchored on the drawing's own reference marks.

Every opening on a plan carries a tag — Dr-02, Wn-01, D1, W3 — placed beside
it. The tag says what the opening is; the wall says where and how wide. For
each tag the nearest axis-aligned room edge is taken as the host wall, and the
wall mask is scanned along that edge for the gap the tag sits beside. Across
the gap a door shows almost nothing, a window shows the thin sill lines; both
are far less than a wall's full thickness, which is the test.

An opening whose gap cannot be found is still reported, centred on the tag at
a nominal width, with `confidence: low` so an assumed width is never mistaken
for a measured one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from app.extractor.grid import DrawingGrid, grid_ref_for_point
from app.extractor.raster import PageRaster
from app.extractor.rooms import MM_PER_PT, Room

# Dr-02, D1, D/3, DR 04, DOOR 2, Wn-01, W3, WIN-2, WINDOW 1, and a trailing letter (D1a).
OPENING_LABEL = re.compile(r"^(?P<kind>DOOR|DR|D|WINDOW|WIN|WN|W)\s*[-/.]?\s*(?P<number>\d{1,3})[A-Za-z]?$", re.IGNORECASE)
LAYERS = {"door": "doors", "window": "windows"}
DEFAULT_WIDTH_MM = {"door": 900.0, "window": 1200.0}

# Real-world distances, converted through the drawing scale.
# A tag sits beside its opening: this far from the host edge at most, and the
# gap is searched this far along the edge either side of the tag.
MAX_TAG_TO_EDGE_MM = 1200.0
SEARCH_ALONG_MM = 1500.0
SCAN_STEP_PT = 0.5

# The band scanned across the wall, and the dark run within it that still
# counts as wall. A wall is 150–300 mm of dark; a window's sill lines total
# 30–60 mm; a door gap is empty.
BAND_MM = 300.0
WALL_DARK_MM = 100.0

MIN_WIDTH_MM = 400.0
MAX_WIDTH_MM = 3000.0


@dataclass
class Opening:
    id: str
    kind: str
    reference: str
    polyline: list[list[float]]
    width_mm: float
    confidence: str
    notes: list[str] = field(default_factory=list)
    host_room_id: str | None = None
    grid_ref: str | None = None

    def to_dict(self) -> dict:
        payload = {
            "id": self.id, "layer": LAYERS[self.kind], "type": self.kind, "reference": self.reference,
            "polyline": self.polyline, "widthMm": self.width_mm, "confidence": self.confidence,
            "notes": self.notes, "hostRoomId": self.host_room_id, "gridRef": self.grid_ref,
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


def _axis_edges(rooms: list[Room]) -> list[tuple[Room, tuple[float, float], tuple[float, float]]]:
    edges = []
    for room in rooms:
        if room.confidence == "low":
            continue
        for a, b in zip(room.polyline, room.polyline[1:] + room.polyline[:1]):
            if (a[0] == b[0]) != (a[1] == b[1]):
                edges.append((room, (a[0], a[1]), (b[0], b[1])))
    return edges


def _nearest_edge(point: tuple[float, float], edges):
    """Closest edge to a point as (room, a, unit, length, distance, offset along edge)."""
    best = None
    for room, a, b in edges:
        length = math.dist(a, b)
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        along = max(0.0, min(length, (point[0] - a[0]) * ux + (point[1] - a[1]) * uy))
        foot = (a[0] + ux * along, a[1] + uy * along)
        distance = math.dist(point, foot)
        if best is None or distance < best[4]:
            best = (room, a, (ux, uy), length, distance, along)
    return best


class _WallScanner:
    """Dark thickness across a wall band, sampled at the raster's resolution."""

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


def detect_openings(cache, raster: PageRaster, rooms: list[Room], grid: DrawingGrid, scale_ratio: float) -> list[Opening]:
    edges = _axis_edges(rooms)
    scanner = _WallScanner(raster)
    mm_per_pt = MM_PER_PT * scale_ratio
    counters = {"door": 0, "window": 0}
    openings: list[Opening] = []

    for kind, reference, x, y in opening_tags(cache.text_spans):
        host = _nearest_edge((x, y), edges) if edges else None
        counters[kind] += 1
        common = dict(kind=kind, reference=reference, grid_ref=grid_ref_for_point(grid, x, y),
                      id=f"{kind}-{counters[kind]:03d}")

        if host is None or host[4] > raster.mm(MAX_TAG_TO_EDGE_MM):
            half = DEFAULT_WIDTH_MM[kind] / 2 / mm_per_pt
            openings.append(Opening(polyline=[[round(x - half, 2), y], [round(x + half, 2), y]],
                                    width_mm=DEFAULT_WIDTH_MM[kind], confidence="low",
                                    notes=["No room wall found near the tag; placed at the tag with an assumed width"], **common))
            continue

        room, a, unit, length, _, along = host
        span = None
        reach = raster.mm(SEARCH_ALONG_MM)
        for offset in sorted(np.arange(-reach, reach + SCAN_STEP_PT, 1.0), key=abs):
            if 0 <= along + offset <= length and (gap := scanner.gap(a, unit, length, along + offset)):
                width_mm = (gap[1] - gap[0]) * mm_per_pt
                if MIN_WIDTH_MM <= width_mm <= MAX_WIDTH_MM:
                    span = gap
                    break

        if span is None:
            half = DEFAULT_WIDTH_MM[kind] / 2 / mm_per_pt
            lo, hi = max(0.0, along - half), min(length, along + half)
            width_mm, confidence = DEFAULT_WIDTH_MM[kind], "low"
            notes = ["No gap found in the wall beside the tag; width is an assumed default"]
        else:
            lo, hi = span
            width_mm, confidence = (hi - lo) * mm_per_pt, "high"
            notes = ["Width measured from the gap in the wall"]

        openings.append(Opening(
            polyline=[[round(float(a[0] + unit[0] * lo), 2), round(float(a[1] + unit[1] * lo), 2)],
                      [round(float(a[0] + unit[0] * hi), 2), round(float(a[1] + unit[1] * hi), 2)]],
            width_mm=float(round(width_mm)), confidence=confidence, notes=notes, host_room_id=room.id, **common,
        ))

    return openings
