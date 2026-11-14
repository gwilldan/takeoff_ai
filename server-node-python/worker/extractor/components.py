"""
Assemble geometry into annotation components.

One component is one thing a quantity surveyor would price: a wall run, a door
opening, a window opening, a column, a room. Each carries its geometry in PDF
points (the space the frontend overlay draws in), a grid reference from the
drawing's own marks, and a confidence.

Openings are represented as the *span across the opening*, not as a point. That
is what they physically are, it renders as a line on the overlay with no new
shape type, and its length is the opening width a bill of quantities will want.

Everything here is pure: caches, dicts and dataclasses in, dicts out.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from extractor.graph import Node, WallRun, mm_to_pts, pts_to_mm
from extractor.grid import DrawingGrid, grid_ref_for_point
from extractor.room_fit import fit_room_to_grid
from extractor.spatial import point_to_segment_distance

LAYER_WALLS = "walls"
LAYER_DOORS = "doors"
LAYER_WINDOWS = "windows"
LAYER_COLUMNS = "columns"
LAYER_ROOMS = "rooms"

# A door swing arc spans a quarter circle whose radius is the door width.
MIN_DOOR_WIDTH_MM = 500.0
MAX_DOOR_WIDTH_MM = 2400.0

# Columns are small closed shapes; anything larger is a room or a building.
MAX_COLUMN_SIDE_MM = 900.0
MIN_COLUMN_SIDE_MM = 100.0

# How near a component has to be to a wall centreline to be hosted by it.
HOST_SEARCH_PTS = 14.0

ROOM_NAME = re.compile(
    r"^(BEDROOM|SITTING ROOM|LOUNGE|LIVING ROOM|DINING|DINING ROOM|KITCHEN|TOILET|"
    r"BATHROOM|WC|LOBBY|CORRIDOR|PASSAGE|PORCH|ENTRANCE PORCH|STORE|STORES|"
    r"VER\.?|VERANDA|VERANDAH|BALCONY|GARAGE|UTILITY|PANTRY|STUDY|OFFICE)$",
    re.IGNORECASE,
)

AREA_LABEL = re.compile(r"(\d+(?:\.\d+)?)\s*(?:m²|m2|sq\.?\s*m)", re.IGNORECASE)


@dataclass
class Component:
    id: str
    layer: str
    type: str
    polyline: list[list[float]]
    confidence: str = "medium"
    reference: str | None = None
    grid_ref: str | None = None
    grid_ref_from: str | None = None
    grid_ref_to: str | None = None
    nodes: list[str] = field(default_factory=list)
    length_mm: float | None = None
    width_mm: float | None = None
    thickness_mm: float | None = None
    area_m2: float | None = None
    host_wall_id: str | None = None
    name: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = {
            "id": self.id,
            "layer": self.layer,
            "type": self.type,
            "polyline": self.polyline,
            "confidence": self.confidence,
        }
        optional = {
            "reference": self.reference,
            "gridRef": self.grid_ref,
            "gridRefFrom": self.grid_ref_from,
            "gridRefTo": self.grid_ref_to,
            "nodes": self.nodes or None,
            "lengthMm": self.length_mm,
            "widthMm": self.width_mm,
            "thicknessMm": self.thickness_mm,
            "areaM2": self.area_m2,
            "hostWallId": self.host_wall_id,
            "name": self.name,
            "notes": self.notes or None,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        return payload


def _nearest_wall(
    walls: list[tuple[str, WallRun]], x: float, y: float, max_distance: float
) -> tuple[str, WallRun] | None:
    best = None
    best_distance = max_distance

    for wall_id, wall in walls:
        distance = point_to_segment_distance(
            x, y, wall.start[0], wall.start[1], wall.end[0], wall.end[1]
        )
        if distance <= best_distance:
            best_distance = distance
            best = (wall_id, wall)

    return best


def wall_components(
    walls: list[WallRun], grid: DrawingGrid, confidence: str = "medium"
) -> list[Component]:
    components = []

    for index, wall in enumerate(walls):
        components.append(Component(
            id=f"w-{index + 1:03d}",
            layer=LAYER_WALLS,
            type="wall",
            polyline=[list(wall.start), list(wall.end)],
            nodes=[wall.start_node, wall.end_node],
            length_mm=wall.length_mm,
            thickness_mm=wall.thickness_mm,
            grid_ref_from=grid_ref_for_point(grid, *wall.start),
            grid_ref_to=grid_ref_for_point(grid, *wall.end),
            confidence=confidence,
        ))

    return components


def _arc_radius_and_hinge(curve: dict) -> tuple[tuple[float, float], float] | None:
    """
    Read a door swing arc as a hinge point and a radius.

    A swing is drawn as a quarter arc from the open leaf back to the frame, so
    the first control point sits on the hinge and the chord to the far end spans
    the opening. Radius comes from the chord: for a quarter circle the chord is
    r*sqrt(2).
    """
    points = curve.get("points") or []
    if len(points) < 2:
        return None

    start = points[0]
    end = points[-1]
    chord = math.hypot(end[0] - start[0], end[1] - start[1])
    if chord <= 0:
        return None

    return ((start[0], start[1]), chord / math.sqrt(2))


def door_components(
    curves: list[dict],
    text_spans: list[dict],
    walls: list[tuple[str, WallRun]],
    grid: DrawingGrid,
    scale_ratio: float,
    door_pattern: str = r"Dr[-\s]?\d+",
) -> list[Component]:
    """
    Derive door openings from swing arcs.

    Arcs are the reliable signal: a door is drawn with one, and its radius is the
    leaf width. Gaps in wall runs are a weaker signal because a wall is also
    interrupted by windows, junctions and hatching boundaries.
    """
    min_radius = mm_to_pts(MIN_DOOR_WIDTH_MM, scale_ratio)
    max_radius = mm_to_pts(MAX_DOOR_WIDTH_MM, scale_ratio)

    references = [
        span for span in text_spans
        if re.match(door_pattern, span["text"].strip(), re.IGNORECASE)
    ]

    components: list[Component] = []
    index = 0

    for curve in curves:
        read = _arc_radius_and_hinge(curve)
        if read is None:
            continue

        hinge, radius = read
        if not (min_radius <= radius <= max_radius):
            continue

        host = _nearest_wall(walls, hinge[0], hinge[1], HOST_SEARCH_PTS)
        if host is None:
            continue

        host_id, wall = host
        span = _opening_span_on_wall(wall, hinge, radius)
        index += 1

        components.append(Component(
            id=f"d-{index:03d}",
            layer=LAYER_DOORS,
            type="door",
            polyline=[list(span[0]), list(span[1])],
            width_mm=round(pts_to_mm(radius, scale_ratio), 1),
            host_wall_id=host_id,
            grid_ref=grid_ref_for_point(grid, *hinge),
            reference=_nearest_reference(references, hinge),
            confidence="high",
        ))

    return components


def _opening_span_on_wall(
    wall: WallRun, at: tuple[float, float], width_pts: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Project an opening of the given width onto the wall centreline."""
    dx = wall.end[0] - wall.start[0]
    dy = wall.end[1] - wall.start[1]
    length = math.hypot(dx, dy)

    if length == 0:
        return (at, at)

    ux, uy = dx / length, dy / length
    t = (at[0] - wall.start[0]) * ux + (at[1] - wall.start[1]) * uy
    t = max(0.0, min(length, t))
    t_end = max(0.0, min(length, t + width_pts))

    return (
        (round(wall.start[0] + ux * t, 2), round(wall.start[1] + uy * t, 2)),
        (round(wall.start[0] + ux * t_end, 2), round(wall.start[1] + uy * t_end, 2)),
    )


def _nearest_reference(
    references: list[dict], point: tuple[float, float], max_distance: float = 60.0
) -> str | None:
    best = None
    best_distance = max_distance

    for span in references:
        distance = math.hypot(span["cx"] - point[0], span["cy"] - point[1])
        if distance <= best_distance:
            best_distance = distance
            best = span["text"].strip()

    return best


def window_components(
    text_spans: list[dict],
    walls: list[tuple[str, WallRun]],
    grid: DrawingGrid,
    scale_ratio: float,
    window_pattern: str = r"Wn[-\s]?\d+",
    default_width_mm: float = 1200.0,
) -> list[Component]:
    """
    Derive window openings from their reference marks.

    Windows have no swing arc, and their in-wall symbol varies far too much
    between offices to detect reliably. The reference label is the dependable
    signal, so a window is placed by projecting its label onto the nearest wall.
    Width is a stated default rather than a measurement, and confidence says so —
    an assumed width must never be mistaken for a measured one.
    """
    components: list[Component] = []
    index = 0

    for span in text_spans:
        text = span["text"].strip()
        if not re.match(window_pattern, text, re.IGNORECASE):
            continue

        # A window label sits beside the wall rather than on it, so the search
        # radius is wider than for a hinge point.
        host = _nearest_wall(walls, span["cx"], span["cy"], HOST_SEARCH_PTS * 3)
        if host is None:
            continue

        host_id, wall = host
        width_pts = mm_to_pts(default_width_mm, scale_ratio)
        start, end = _opening_span_on_wall(wall, (span["cx"], span["cy"]), width_pts)
        index += 1

        components.append(Component(
            id=f"win-{index:03d}",
            layer=LAYER_WINDOWS,
            type="window",
            polyline=[list(start), list(end)],
            width_mm=default_width_mm,
            host_wall_id=host_id,
            reference=text,
            grid_ref=grid_ref_for_point(grid, span["cx"], span["cy"]),
            confidence="low",
            notes=["Width is an assumed default, not measured from the drawing"],
        ))

    return components


def column_components(
    rects: list[dict], grid: DrawingGrid, scale_ratio: float
) -> list[Component]:
    """Small closed rectangles are columns; large ones are rooms or the sheet."""
    min_side = mm_to_pts(MIN_COLUMN_SIDE_MM, scale_ratio)
    max_side = mm_to_pts(MAX_COLUMN_SIDE_MM, scale_ratio)

    components: list[Component] = []
    index = 0

    for rect in rects:
        width = rect["width_pts"]
        height = rect["height_pts"]

        if not (min_side <= width <= max_side and min_side <= height <= max_side):
            continue

        index += 1
        x, y, x2, y2 = rect["x"], rect["y"], rect["x2"], rect["y2"]
        cx, cy = (x + x2) / 2, (y + y2) / 2

        components.append(Component(
            id=f"col-{index:03d}",
            layer=LAYER_COLUMNS,
            type="column",
            polyline=[[x, y], [x2, y], [x2, y2], [x, y2]],
            width_mm=round(pts_to_mm(width, scale_ratio), 1),
            thickness_mm=round(pts_to_mm(height, scale_ratio), 1),
            grid_ref=grid_ref_for_point(grid, cx, cy),
            confidence="medium",
        ))

    return components


# A room tag prints its area beside its name — on the drawings measured so far,
# exactly 10.4 pt below it. This radius allows for other stacking conventions
# while staying far tighter than the room spacing, so a tag cannot be claimed by
# a neighbouring room.
ROOM_TAG_PAIR_RADIUS_PTS = 30.0

# A name-like label: uppercase words, allowing spaces and the "VER." style stop.
ROOM_NAME_SHAPE = re.compile(r"^[A-Z][A-Z. ]{1,28}$")


def _pair_area_tags_to_names(text_spans: list[dict]) -> list[tuple[dict, float | None]]:
    """
    Match each printed area tag to its room name, one to one.

    Anchoring on the area tag rather than a list of known room names is what
    makes this work on any drawing: the name is whatever label sits beside the
    area, so SCULLERY or CHAMBRE reads as readily as BEDROOM. Pairs are assigned
    nearest-first and each span is consumed once — matching every name to its
    closest tag independently scrambles them, attaching one room's area to its
    neighbour.
    """
    areas = []
    for span in text_spans:
        match = AREA_LABEL.search(span["text"].strip())
        if match and AREA_LABEL.fullmatch(span["text"].strip()):
            areas.append((span, float(match.group(1))))

    names = [
        span for span in text_spans
        if ROOM_NAME_SHAPE.match(span["text"].strip())
        and not AREA_LABEL.search(span["text"])
    ]

    candidates = []
    for area_span, value in areas:
        for name_span in names:
            distance = math.hypot(
                name_span["cx"] - area_span["cx"], name_span["cy"] - area_span["cy"]
            )
            if distance <= ROOM_TAG_PAIR_RADIUS_PTS:
                candidates.append((distance, area_span, value, name_span))

    candidates.sort(key=lambda c: c[0])

    used_areas: set[int] = set()
    used_names: set[int] = set()
    paired: list[tuple[dict, float | None]] = []

    for _, area_span, value, name_span in candidates:
        if id(area_span) in used_areas or id(name_span) in used_names:
            continue
        used_areas.add(id(area_span))
        used_names.add(id(name_span))
        paired.append((name_span, value))

    # Drawings that name rooms without printing areas still yield rooms, but only
    # for names we recognise — an arbitrary uppercase label with no area beside it
    # is as likely to be a note or a title as a room.
    for name_span in names:
        if id(name_span) not in used_names and ROOM_NAME.match(name_span["text"].strip()):
            paired.append((name_span, None))

    return paired


def room_components(
    text_spans: list[dict],
    walls: list[tuple[str, WallRun]],
    grid: DrawingGrid,
    scale_ratio: float,
) -> list[Component]:
    """
    Rooms from their tags: name, printed area, and an area-correct extent.

    A true room polygon needs a boundary trace, which the vector geometry does
    not reliably support across drawing styles. Where the drawing prints an area,
    the placeholder square is sized to *match that area* — so it is wrong in
    shape but right in quantity, which is the half that a bill of quantities
    depends on. The notes say exactly that.
    """
    paired = _pair_area_tags_to_names(text_spans)
    all_tags = [(span["cx"], span["cy"]) for span, _ in paired]

    components: list[Component] = []

    for index, (name_span, area_m2) in enumerate(paired, start=1):
        cx, cy = name_span["cx"], name_span["cy"]

        fit = None
        if area_m2:
            others = [t for t in all_tags if t != (cx, cy)]
            fit = fit_room_to_grid((cx, cy), area_m2, others, grid, scale_ratio)

        if fit is not None:
            polyline = fit.polyline
            grid_ref = fit.grid_ref
            confidence = "high" if fit.area_error <= 0.05 else "medium"
            notes = [
                f"Outline snapped to the setting-out grid; area is within "
                f"{fit.area_error * 100:.0f}% of the printed {area_m2} m²"
            ]
        else:
            # The grid cannot express this room — usually a small room in a coarse
            # region. A square of the right area is honest: wrong in shape, right
            # in the quantity a bill of quantities depends on.
            side_mm = math.sqrt(area_m2) * 1000 if area_m2 else 3000
            half = mm_to_pts(side_mm / 2, scale_ratio)
            polyline = [
                [round(cx - half, 2), round(cy - half, 2)],
                [round(cx + half, 2), round(cy - half, 2)],
                [round(cx + half, 2), round(cy + half, 2)],
                [round(cx - half, 2), round(cy + half, 2)],
            ]
            grid_ref = grid_ref_for_point(grid, cx, cy)
            confidence = "medium" if area_m2 else "low"
            notes = [
                "No grid rectangle matched the printed area; extent is a square of "
                "the correct area, approximate in shape"
                if area_m2
                else "No printed area found; extent is a nominal box around the label"
            ]

        components.append(Component(
            id=f"room-{index:03d}",
            layer=LAYER_ROOMS,
            type="room",
            name=name_span["text"].strip().upper(),
            polyline=polyline,
            area_m2=area_m2,
            grid_ref=grid_ref,
            confidence=confidence,
            notes=notes,
        ))

    return components


def build_components(
    cache,
    profile: dict,
    nodes: list[Node],
    walls: list[WallRun],
    grid: DrawingGrid,
) -> list[Component]:
    """Assemble every component class for one page, walls first."""
    scale_ratio = float(profile.get("scale_ratio") or 100)
    opening_style = profile.get("opening_style") or {}

    wall_parts = wall_components(walls, grid, profile.get("confidence", "medium"))
    indexed_walls = [(component.id, wall) for component, wall in zip(wall_parts, walls)]

    return [
        *wall_parts,
        *door_components(
            cache.curves,
            cache.text_spans,
            indexed_walls,
            grid,
            scale_ratio,
            opening_style.get("door_pattern") or r"Dr[-\s]?\d+",
        ),
        *window_components(
            cache.text_spans,
            indexed_walls,
            grid,
            scale_ratio,
            opening_style.get("window_pattern") or r"Wn[-\s]?\d+",
        ),
        *column_components(cache.rects, grid, scale_ratio),
        *room_components(cache.text_spans, indexed_walls, grid, scale_ratio),
    ]
