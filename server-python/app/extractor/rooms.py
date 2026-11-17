"""
Rooms: the pockets the walls enclose, named by the drawing's own labels.

Rooms come from the walls, not the labels (`walls.py` seals the openings and
leaves free pockets), so an unlabelled room, or a scan whose text cannot be
read, still yields rooms. Labels then do two jobs:

- Naming. A room tag is a name with its printed area beside it (the name is
  whatever label sits next to an area, so SCULLERY reads as readily as
  BEDROOM); a bare name is accepted from a known vocabulary, and a pocket
  with no tag takes the most prominent name-like text inside it. Pockets
  still unnamed are named by the vision model (`labelling.py`).
- Checking. Where a printed area disagrees with its pocket — a wall the
  reading missed, two rooms in one pocket — the room is traced again around
  its own label: the wall mask is closed with increasing radii and the first
  pocket that matches the printed area wins. A labelled room that cannot be
  traced either way falls back to a square of the printed area, and says so.

Corners are clustered into page-level nodes so adjoining rooms share them,
and every node carries a grid reference from the drawing's own marks.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from app.extractor.grid import DrawingGrid, grid_ref_for_point
from app.extractor.raster import PageRaster, polygon_area_pts2
from app.extractor.walls import Pocket, WallReading

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
ROOM_NAME_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9.'/ -]{1,30}$")
# "12 m²", "12m2", "12 sq m", and "12 M" whose ² is a separate superscript span.
AREA_LABEL = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(?:m²|m2|sq\.?\s*m|sqm|M)\.?$", re.IGNORECASE)

# A name and its area are stacked a line or two apart; this stays well inside
# the spacing between rooms so a tag cannot be claimed by a neighbour. Large
# lettering sits further from its area, so the reach grows with the name.
TAG_PAIR_RADIUS_PT = 30.0
TAG_PAIR_FONT_LINES = 3.5

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


# Text inside a pocket that is not a room name: dimensions, levels, grid and
# opening marks, areas.
NOT_A_NAME = re.compile(r"^[\d\s.,:+\-/x×]*$|^[A-Za-z]{0,2}\s*[-/.]?\s*\d{0,3}[A-Za-z]?$", re.IGNORECASE)

UNNAMED = "ROOM"


@dataclass
class Room:
    id: str
    name: str
    polyline: list[list[float]]
    confidence: str
    notes: list[str]
    source: str  # "walls" (a sealed pocket) or "label" (traced around its tag)
    area_m2: float | None = None
    measured_area_m2: float | None = None
    grid_ref: str | None = None
    nodes: list[str] = field(default_factory=list)
    pocket: int | None = None  # free-space label in the wall reading

    def to_dict(self) -> dict:
        payload = {
            "id": self.id, "layer": LAYER, "type": "room", "name": self.name,
            "polyline": self.polyline, "confidence": self.confidence, "notes": self.notes,
            "source": self.source, "areaM2": self.area_m2, "measuredAreaM2": self.measured_area_m2,
            "gridRef": self.grid_ref, "nodes": self.nodes,
        }
        return {k: v for k, v in payload.items() if v is not None}


def pair_room_tags(text_spans: list[dict]) -> list[RoomTag]:
    """Match each printed area to its name, nearest first, each span used once."""
    areas = [(s, float(m.group(1).replace(",", "."))) for s in text_spans if (m := AREA_LABEL.match(s["text"]))]
    # A short code (P1, D5, "C D") is a mark, not a name, unless it is a known room word (WC).
    names = [s for s in text_spans if ROOM_NAME_SHAPE.match(s["text"]) and not AREA_LABEL.match(s["text"])
             and (ROOM_NAME.match(s["text"]) or sum(ch.isalpha() for ch in s["text"]) >= 3)
             and not NOT_A_NAME.match(s["text"])]

    candidates = sorted(
        (math.dist((n["cx"], n["cy"]), (a["cx"], a["cy"])), id(a), id(n), a, value, n)
        for a, value in areas for n in names
        if math.dist((n["cx"], n["cy"]), (a["cx"], a["cy"])) <= max(TAG_PAIR_RADIUS_PT, TAG_PAIR_FONT_LINES * n.get("font_size", 0))
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


def share_nodes(rooms: list[Room], grid: DrawingGrid) -> list[dict]:
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


def _pocket_polygon(reading: WallReading, pocket: Pocket, raster: PageRaster) -> list[list[float]]:
    x, y, w, h = pocket.box
    region = (reading.labels[y:y + h, x:x + w] == pocket.label).astype(np.uint8)
    return raster.polygon(region, origin=(x, y))


def _inside(reading: WallReading, pocket: Pocket, raster: PageRaster, x: float, y: float) -> bool:
    found = reading.pocket_at(raster, x, y)
    return found is not None and found.label == pocket.label


def _pocket_name(cache, reading: WallReading, pocket: Pocket, raster: PageRaster) -> str | None:
    """The most prominent name-like text inside an untagged pocket."""
    candidates = [
        s for s in cache.text_spans
        if ROOM_NAME_SHAPE.match(s["text"]) and not NOT_A_NAME.match(s["text"]) and not AREA_LABEL.match(s["text"])
        and _inside(reading, pocket, raster, s["cx"], s["cy"])
    ]
    return max(candidates, key=lambda s: s.get("font_size", 0))["text"].upper() if candidates else None


def _error(polyline: list[list[float]], printed: float | None, scale_ratio: float) -> float | None:
    return abs(_m2(polygon_area_pts2(polyline), scale_ratio) - printed) / printed if printed else None


def _within(error: float | None, printed: float | None) -> bool:
    return error is not None and error <= max(AREA_TOLERANCE, AREA_ROUNDING_M2 / printed)


def detect_rooms(cache, raster: PageRaster, reading: WallReading, grid: DrawingGrid, scale_ratio: float) -> list[Room]:
    """Every room on the page. Corner nodes are shared afterwards (`share_nodes`)."""
    tags = pair_room_tags(cache.text_spans)
    rooms: list[Room] = []

    def add(name, polyline, confidence, notes, source, tag=None, pocket=None):
        anchor = (tag.x, tag.y) if tag else tuple(np.mean(polyline, axis=0))
        rooms.append(Room(
            id=f"room-{len(rooms) + 1:03d}", name=name, polyline=polyline, confidence=confidence, notes=notes,
            source=source, area_m2=tag.area_m2 if tag else None,
            measured_area_m2=round(_m2(polygon_area_pts2(polyline), scale_ratio), 2),
            grid_ref=grid_ref_for_point(grid, *anchor), pocket=pocket.label if pocket else None,
        ))

    def retrace(tag: RoomTag) -> None:
        """A tag its pocket could not account for: trace around the label, or fall back."""
        if traced := _trace(tag, raster, scale_ratio):
            polyline, error = traced
            confidence = "high" if tag.area_m2 and error <= GOOD_ENOUGH else "medium"
            note = (f"Outline traced around its label; area within {error * 100:.0f}% of the printed {tag.area_m2} m²"
                    if tag.area_m2 else "Outline traced around its label; no printed area to check against")
            add(tag.name, polyline, confidence, [note], "label", tag)
        else:
            add(tag.name, _fallback_square(tag, scale_ratio), "low",
                ["Outline could not be traced; extent is a square of the printed area" if tag.area_m2
                 else "Outline could not be traced; extent is a nominal box around the label"], "label", tag)

    claimed: set[int] = set()
    for pocket in reading.pockets:
        polyline = _pocket_polygon(reading, pocket, raster)
        if len(polyline) < 3:
            continue
        inside = [t for t in tags if _inside(reading, pocket, raster, t.x, t.y)]
        claimed.update(id(t) for t in inside)

        if not inside:
            name = _pocket_name(cache, reading, pocket, raster)
            add(name or UNNAMED, polyline, "medium",
                ["Enclosed by walls; " + ("named from the text inside it" if name else "no label inside it")], "walls", pocket=pocket)
            continue

        if len(inside) == 1:
            tag = inside[0]
            error = _error(polyline, tag.area_m2, scale_ratio)
            if tag.area_m2 is None or _within(error, tag.area_m2):
                confidence = "high" if error is not None and error <= GOOD_ENOUGH else "medium"
                note = (f"Enclosed by walls; area within {error * 100:.0f}% of the printed {tag.area_m2} m²"
                        if error is not None else "Enclosed by walls; no printed area to check against")
                add(tag.name, polyline, confidence, [note], "walls", tag, pocket)
            else:
                retrace(tag)
            continue

        # Several tags in one pocket: an open plan, or a wall the reading missed.
        printed = [t.area_m2 for t in inside]
        if all(printed) and _within(_error(polyline, sum(printed), scale_ratio), sum(printed)):
            add(" / ".join(t.name for t in inside), polyline, "medium",
                [f"One open space holding {len(inside)} labelled areas totalling {sum(printed):g} m²"], "walls", pocket=pocket)
        else:
            for tag in inside:
                retrace(tag)

    for tag in tags:
        if id(tag) not in claimed:
            retrace(tag)

    return rooms

