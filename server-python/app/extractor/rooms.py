"""
Rooms: read the tags, trace the outlines, share the corners.

A room tag is a name with its printed area beside it. The name is whatever
label sits next to an area, so SCULLERY reads as readily as BEDROOM; a bare
name with no area is accepted only from a known vocabulary.

The printed area is also the oracle. Door openings are bridged by closing the
wall mask with increasing radii, and the first radius whose pocket matches
the printed area wins — the drawing's own annotation decides, not a guessed
threshold. A room whose outline cannot be traced falls back to a square of
the printed area, and says so.

Corners are clustered into page-level nodes so adjoining rooms share them,
and every node carries a grid reference from the drawing's own marks.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.extractor.grid import DrawingGrid, grid_ref_for_point
from app.extractor.raster import PageRaster, polygon_area_pts2

LAYER = "rooms"

MM_PER_PT = 25.4 / 72

# Names accepted without a printed area beside them. With an area, any label counts.
ROOM_NAME = re.compile(
    r"^(?:MASTER |GUEST |MAIN |ENTRANCE |FAMILY |VISITORS?' ?)?"
    r"(BEDROOM|BED|SITTING ROOM|LOUNGE|LIVING ROOM|LIVING|PARLOUR|DINING|DINING ROOM|KITCHEN|"
    r"KITCHENETTE|TOILET|BATHROOM|BATH|WC|W\.C\.?|ENSUITE|EN-SUITE|SHOWER|LAUNDRY|LOBBY|HALL|"
    r"HALLWAY|CORRIDOR|PASSAGE|PORCH|VER\.?|VERANDA|VERANDAH|BALCONY|TERRACE|PATIO|GARAGE|"
    r"CARPORT|STORE|STORES|STORAGE|UTILITY|PANTRY|STUDY|OFFICE|RECEPTION|WAITING|WARD|"
    r"CLASSROOM|LABORATORY|LAB|LIBRARY|SHOP|WORKSHOP|PLANT ROOM|SERVER ROOM|STAIR|STAIRS|"
    r"STAIRCASE|LIFT|CONFERENCE|MEETING ROOM|BOARDROOM|CHAPEL|MOSQUE|GYM|CLINIC|CONSULTING)$",
    re.IGNORECASE,
)
ROOM_NAME_SHAPE = re.compile(r"^[A-Z][A-Z0-9.'/ -]{1,30}$")
AREA_LABEL = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(?:m²|m2|sq\.?\s*m|sqm)\.?$", re.IGNORECASE)

# A name and its area are stacked a line apart; this stays well inside the
# spacing between rooms so a tag cannot be claimed by a neighbour.
TAG_PAIR_RADIUS_PT = 30.0

# Closing radii tried in order, in real millimetres; a radius bridges a gap up
# to twice its size, so 600 mm closes a 1.2 m double door. The first radius
# whose pocket matches the printed area wins.
CLOSING_RADII_MM = (0.0, 150.0, 300.0, 450.0, 600.0)
RADIUS_WITHOUT_AREA_MM = 300.0

# Accept a pocket whose area is within this share of the printed figure, or
# within the half-unit the printed figure was rounded by, whichever is looser.
AREA_TOLERANCE = 0.12
AREA_ROUNDING_M2 = 0.5
GOOD_ENOUGH = 0.05
MAX_ROOM_M2 = 120.0

NODE_TOLERANCE_PT = 2.0


@dataclass
class RoomTag:
    name: str
    x: float
    y: float
    area_m2: float | None


@dataclass
class Room:
    id: str
    name: str
    polyline: list[list[float]]
    confidence: str
    notes: list[str]
    area_m2: float | None = None
    measured_area_m2: float | None = None
    grid_ref: str | None = None
    nodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = {
            "id": self.id, "layer": LAYER, "type": "room", "name": self.name,
            "polyline": self.polyline, "confidence": self.confidence, "notes": self.notes,
            "areaM2": self.area_m2, "measuredAreaM2": self.measured_area_m2,
            "gridRef": self.grid_ref, "nodes": self.nodes,
        }
        return {k: v for k, v in payload.items() if v is not None}


def pair_room_tags(text_spans: list[dict]) -> list[RoomTag]:
    """Match each printed area to its name, nearest first, each span used once."""
    areas = [(s, float(m.group(1).replace(",", "."))) for s in text_spans if (m := AREA_LABEL.match(s["text"]))]
    names = [s for s in text_spans if ROOM_NAME_SHAPE.match(s["text"]) and not AREA_LABEL.match(s["text"])]

    candidates = sorted(
        (math.dist((n["cx"], n["cy"]), (a["cx"], a["cy"])), id(a), id(n), a, value, n)
        for a, value in areas for n in names
        if math.dist((n["cx"], n["cy"]), (a["cx"], a["cy"])) <= TAG_PAIR_RADIUS_PT
    )

    used: set[int] = set()
    tags: list[RoomTag] = []
    for _, area_id, name_id, _, value, name in candidates:
        if area_id in used or name_id in used:
            continue
        used.update((area_id, name_id))
        tags.append(RoomTag(name["text"].upper(), name["cx"], name["cy"], value))

    tags.extend(
        RoomTag(n["text"].upper(), n["cx"], n["cy"], None)
        for n in names if id(n) not in used and ROOM_NAME.match(n["text"])
    )
    return tags


def _m2(area_pts2: float, scale_ratio: float) -> float:
    return area_pts2 * (MM_PER_PT * scale_ratio) ** 2 / 1_000_000


def _trace(tag: RoomTag, raster: PageRaster, scale_ratio: float) -> tuple[list[list[float]], float] | None:
    """Best outline for one tag as (polyline, area error), or None."""
    radii = CLOSING_RADII_MM if tag.area_m2 else (RADIUS_WITHOUT_AREA_MM,)
    best: tuple[float, list[list[float]]] | None = None

    for radius_mm in radii:
        region = raster.region_around((tag.x, tag.y), raster.mm(radius_mm))
        if region is None or _m2(raster.area_pts2(region), scale_ratio) > MAX_ROOM_M2:
            continue  # walled in, or leaked outside the building
        polygon = raster.polygon(region)
        if len(polygon) < 3:
            continue
        # Judge the outline that will be reported, not the raw fill.
        area = _m2(polygon_area_pts2(polygon), scale_ratio)
        error = abs(area - tag.area_m2) / tag.area_m2 if tag.area_m2 else 0.0
        if best is None or error < best[0]:
            best = (error, polygon)
        if error <= GOOD_ENOUGH:
            break

    if best is None or (tag.area_m2 and best[0] > max(AREA_TOLERANCE, AREA_ROUNDING_M2 / tag.area_m2)):
        return None
    return best[1], best[0]


def _fallback_square(tag: RoomTag, scale_ratio: float) -> list[list[float]]:
    side_mm = math.sqrt(tag.area_m2) * 1000 if tag.area_m2 else 3000
    half = side_mm / 2 / (MM_PER_PT * scale_ratio)
    return [[round(tag.x + dx * half, 2), round(tag.y + dy * half, 2)] for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def _share_nodes(rooms: list[Room], grid: DrawingGrid) -> list[dict]:
    """Cluster corners within NODE_TOLERANCE_PT into page nodes; rooms reference them by id."""
    centres: list[list[float]] = []
    for room in rooms:
        for x, y in room.polyline:
            for index, (nx, ny) in enumerate(centres):
                if math.dist((x, y), (nx, ny)) <= NODE_TOLERANCE_PT:
                    room.nodes.append(f"n{index + 1}")
                    break
            else:
                centres.append([x, y])
                room.nodes.append(f"n{len(centres)}")

    return [
        {"id": f"n{i + 1}", "point": [round(x, 2), round(y, 2)], "gridRef": grid_ref_for_point(grid, x, y)}
        for i, (x, y) in enumerate(centres)
    ]


def detect_rooms(cache, raster: PageRaster, grid: DrawingGrid, scale_ratio: float) -> tuple[list[dict], list[Room]]:
    """Every room on the page, plus the shared corner nodes they reference."""
    rooms: list[Room] = []

    for index, tag in enumerate(pair_room_tags(cache.text_spans), start=1):
        traced = _trace(tag, raster, scale_ratio)

        if traced:
            polyline, error = traced
            confidence = "high" if tag.area_m2 and error <= GOOD_ENOUGH else "medium"
            notes = [f"Outline traced from the drawing; area within {error * 100:.0f}% of the printed {tag.area_m2} m²"] if tag.area_m2 else ["Outline traced from the drawing; no printed area to check against"]
        else:
            polyline = _fallback_square(tag, scale_ratio)
            confidence = "low"
            notes = ["Outline could not be traced; extent is a square of the printed area" if tag.area_m2 else "Outline could not be traced; extent is a nominal box around the label"]

        rooms.append(Room(
            id=f"room-{index:03d}", name=tag.name, polyline=polyline, confidence=confidence, notes=notes,
            area_m2=tag.area_m2, measured_area_m2=round(_m2(polygon_area_pts2(polyline), scale_ratio), 2),
            grid_ref=grid_ref_for_point(grid, tag.x, tag.y),
        ))

    return _share_nodes(rooms, grid), rooms
