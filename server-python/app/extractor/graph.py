"""
Wall geometry — from loose line segments to a node graph of wall centrelines.

A wall on a drawing is not one line. It is two parallel faces, usually with
hatching between them, so a colour filter over raw segments yields hundreds of
fragments that cannot be annotated or priced. This module reconstructs the thing
the draughtsman meant: pair the faces, take the midline, measure the gap as a
thickness, then snap endpoints so walls that meet share a node.

Everything here is pure — plain dicts and tuples in, dataclasses out, no PDF
handles and no model calls. That is what makes it testable, and the tolerances
below are exactly the sort of thing that silently shifts every annotation on the
sheet if it drifts, so it is tested hard.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PDF_POINTS_PER_INCH = 72
MM_PER_INCH = 25.4

# Angle within which two segments count as parallel.
DEFAULT_ANGLE_TOLERANCE_DEG = 2.0

# A pairing needs this much shared run before it is a wall rather than a
# coincidence between two unrelated lines.
DEFAULT_MIN_OVERLAP_PTS = 12.0

# Endpoints closer than this become one node.
DEFAULT_NODE_TOLERANCE_PTS = 2.0

# Real-world wall thickness bounds. Below the lower bound is hatching or a
# double-drawn line; above it is a room, not a wall.
MIN_WALL_THICKNESS_MM = 60.0
MAX_WALL_THICKNESS_MM = 600.0


def mm_to_pts(mm: float, scale_ratio: float) -> float:
    """Millimetres of real world to points on the sheet at the given scale."""
    return mm / ((MM_PER_INCH / PDF_POINTS_PER_INCH) * scale_ratio)


def pts_to_mm(pts: float, scale_ratio: float) -> float:
    return pts * (MM_PER_INCH / PDF_POINTS_PER_INCH) * scale_ratio


@dataclass
class Node:
    id: str
    x: float
    y: float

    def to_dict(self, grid_ref: str | None = None) -> dict:
        return {"id": self.id, "point": [self.x, self.y], "gridRef": grid_ref}


@dataclass
class WallRun:
    start: tuple[float, float]
    end: tuple[float, float]
    thickness_pts: float
    thickness_mm: float
    length_pts: float
    length_mm: float
    start_node: str = ""
    end_node: str = ""


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length == 0:
        return (0.0, 0.0)
    return (dx / length, dy / length)


def _angle_deg(dx: float, dy: float) -> float:
    """Undirected orientation in [0, 180) — a segment drawn either way matches."""
    return math.degrees(math.atan2(dy, dx)) % 180.0


def _angles_close(a: float, b: float, tolerance: float) -> bool:
    diff = abs(a - b) % 180.0
    return min(diff, 180.0 - diff) <= tolerance


def select_wall_face_segments(
    segments: list[dict],
    *,
    include_colors: list[list[float]] | None = None,
    exclude_colors: list[list[float]] | None = None,
    min_stroke: float = 0.0,
    max_stroke: float | None = None,
    min_length_pts: float = 6.0,
    color_tolerance: float = 0.15,
) -> list[dict]:
    """
    Narrow raw segments down to plausible wall faces.

    Colour exclusion is the load-bearing filter. A setting-out grid line often
    runs at exactly a wall-thickness offset from a wall face, and without
    dropping grid colours that pair reads as a wall.
    """
    include_colors = include_colors or []
    exclude_colors = exclude_colors or []

    def matches(color: list[float] | None, palette: list[list[float]]) -> bool:
        if color is None or not palette:
            return False
        return any(
            len(entry) >= 3
            and all(abs(color[i] - entry[i]) <= color_tolerance for i in range(3))
            for entry in palette
        )

    selected: list[dict] = []

    for segment in segments:
        if segment.get("length_pts", 0) < min_length_pts:
            continue

        stroke = segment.get("stroke_width") or 0
        if stroke < min_stroke:
            continue
        if max_stroke is not None and stroke > max_stroke:
            continue

        color = segment.get("color_rgb")
        if matches(color, exclude_colors):
            continue
        if include_colors and not matches(color, include_colors):
            continue

        selected.append(segment)

    return selected


def points_to_nodes(
    points: list[tuple[float, float]],
    tolerance: float = DEFAULT_NODE_TOLERANCE_PTS,
) -> tuple[list[Node], dict[tuple[float, float], str]]:
    """
    Cluster nearby points into shared nodes.

    Returns the nodes and a lookup from each original point to its node id, so
    callers can rewrite their geometry in terms of the graph.
    """
    clusters: list[list[tuple[float, float]]] = []
    assignment: list[int] = []

    for point in points:
        for index, cluster in enumerate(clusters):
            first = cluster[0]
            if math.hypot(point[0] - first[0], point[1] - first[1]) <= tolerance:
                cluster.append(point)
                assignment.append(index)
                break
        else:
            clusters.append([point])
            assignment.append(len(clusters) - 1)

    nodes = [
        Node(
            id=f"n{index + 1}",
            x=round(sum(p[0] for p in cluster) / len(cluster), 2),
            y=round(sum(p[1] for p in cluster) / len(cluster), 2),
        )
        for index, cluster in enumerate(clusters)
    ]

    index_map: dict[tuple[float, float], str] = {}
    for point, cluster_index in zip(points, assignment):
        index_map[point] = nodes[cluster_index].id

    return nodes, index_map


def _project_span(
    segment: dict, origin: tuple[float, float], direction: tuple[float, float]
) -> tuple[float, float]:
    """Where a segment starts and ends along a reference direction."""
    values = []
    for key in ("start", "end"):
        px, py = segment[key]
        values.append((px - origin[0]) * direction[0] + (py - origin[1]) * direction[1])
    return (min(values), max(values))


def pair_wall_faces(
    segments: list[dict],
    *,
    min_thickness_pts: float,
    max_thickness_pts: float,
    angle_tolerance_deg: float = DEFAULT_ANGLE_TOLERANCE_DEG,
    min_overlap_pts: float = DEFAULT_MIN_OVERLAP_PTS,
    scale_ratio: float = 100.0,
) -> list[WallRun]:
    """
    Pair parallel faces into wall centrelines.

    Candidates are scored by shared run length and consumed greedily, so one
    physical face is never claimed by two walls — three evenly spaced parallel
    lines yield one wall, not two overlapping ones.
    """
    prepared = []
    for index, segment in enumerate(segments):
        sx, sy = segment["start"]
        ex, ey = segment["end"]
        dx, dy = ex - sx, ey - sy
        if dx == 0 and dy == 0:
            continue
        prepared.append({
            "index": index,
            "segment": segment,
            "origin": (sx, sy),
            "unit": _unit(dx, dy),
            "angle": _angle_deg(dx, dy),
        })

    candidates = []

    for i in range(len(prepared)):
        for j in range(i + 1, len(prepared)):
            a, b = prepared[i], prepared[j]

            if not _angles_close(a["angle"], b["angle"], angle_tolerance_deg):
                continue

            ux, uy = a["unit"]
            nx, ny = -uy, ux

            bx, by = b["origin"]
            perpendicular = (bx - a["origin"][0]) * nx + (by - a["origin"][1]) * ny
            thickness = abs(perpendicular)

            if not (min_thickness_pts <= thickness <= max_thickness_pts):
                continue

            a_lo, a_hi = _project_span(a["segment"], a["origin"], a["unit"])
            b_lo, b_hi = _project_span(b["segment"], a["origin"], a["unit"])
            lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
            overlap = hi - lo

            if overlap < min_overlap_pts:
                continue

            candidates.append({
                "i": a["index"],
                "j": b["index"],
                "overlap": overlap,
                "thickness": thickness,
                "origin": a["origin"],
                "unit": (ux, uy),
                "normal": (nx, ny),
                "offset": perpendicular / 2.0,
                "lo": lo,
                "hi": hi,
            })

    # Longest shared run wins, so a full-length pairing beats a stub.
    candidates.sort(key=lambda c: c["overlap"], reverse=True)

    used: set[int] = set()
    walls: list[WallRun] = []

    for candidate in candidates:
        if candidate["i"] in used or candidate["j"] in used:
            continue

        used.add(candidate["i"])
        used.add(candidate["j"])

        ox, oy = candidate["origin"]
        ux, uy = candidate["unit"]
        nx, ny = candidate["normal"]
        offset = candidate["offset"]

        start = (
            round(ox + ux * candidate["lo"] + nx * offset, 2),
            round(oy + uy * candidate["lo"] + ny * offset, 2),
        )
        end = (
            round(ox + ux * candidate["hi"] + nx * offset, 2),
            round(oy + uy * candidate["hi"] + ny * offset, 2),
        )
        length_pts = math.hypot(end[0] - start[0], end[1] - start[1])

        walls.append(WallRun(
            start=start,
            end=end,
            thickness_pts=round(candidate["thickness"], 2),
            thickness_mm=round(pts_to_mm(candidate["thickness"], scale_ratio), 1),
            length_pts=round(length_pts, 2),
            length_mm=round(pts_to_mm(length_pts, scale_ratio), 1),
        ))

    return walls


def merge_collinear(
    walls: list[WallRun],
    *,
    tolerance_pts: float = DEFAULT_NODE_TOLERANCE_PTS,
    angle_tolerance_deg: float = DEFAULT_ANGLE_TOLERANCE_DEG,
    scale_ratio: float = 100.0,
) -> list[WallRun]:
    """
    Join wall runs that lie on the same line and touch end to end.

    Walls interrupted by a doorway must stay separate — that gap is where the
    opening lives — so only runs whose endpoints actually meet are merged.
    """
    remaining = list(walls)
    merged: list[WallRun] = []

    while remaining:
        current = remaining.pop(0)
        changed = True

        while changed:
            changed = False
            for other in list(remaining):
                if abs(current.thickness_pts - other.thickness_pts) > tolerance_pts:
                    continue

                current_angle = _angle_deg(
                    current.end[0] - current.start[0], current.end[1] - current.start[1]
                )
                other_angle = _angle_deg(
                    other.end[0] - other.start[0], other.end[1] - other.start[1]
                )
                if not _angles_close(current_angle, other_angle, angle_tolerance_deg):
                    continue

                joined = _join_if_touching(current, other, tolerance_pts, scale_ratio)
                if joined is not None:
                    current = joined
                    remaining.remove(other)
                    changed = True

        merged.append(current)

    return merged


def _join_if_touching(
    a: WallRun, b: WallRun, tolerance: float, scale_ratio: float
) -> WallRun | None:
    pairs = (
        (a.end, b.start, a.start, b.end),
        (a.end, b.end, a.start, b.start),
        (a.start, b.start, a.end, b.end),
        (a.start, b.end, a.end, b.start),
    )

    for meet_a, meet_b, far_a, far_b in pairs:
        if math.hypot(meet_a[0] - meet_b[0], meet_a[1] - meet_b[1]) <= tolerance:
            length_pts = math.hypot(far_b[0] - far_a[0], far_b[1] - far_a[1])
            return WallRun(
                start=far_a,
                end=far_b,
                thickness_pts=round((a.thickness_pts + b.thickness_pts) / 2, 2),
                thickness_mm=round((a.thickness_mm + b.thickness_mm) / 2, 1),
                length_pts=round(length_pts, 2),
                length_mm=round(pts_to_mm(length_pts, scale_ratio), 1),
            )

    return None


def _line_intersection(
    a: WallRun, b: WallRun
) -> tuple[float, float] | None:
    """Intersection of two infinite centrelines, or None when parallel."""
    x1, y1 = a.start
    x2, y2 = a.end
    x3, y3 = b.start
    x4, y4 = b.end

    denominator = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(denominator) < 1e-9:
        return None

    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / denominator

    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def connect_joints(
    walls: list[WallRun],
    *,
    scale_ratio: float = 100.0,
    reach_factor: float = 1.2,
) -> list[WallRun]:
    """
    Extend or trim wall centrelines to meet at corners.

    Two walls meeting at a corner do not share a centreline endpoint: each stops
    roughly half the other wall's thickness short of the crossing point. Left
    alone the graph is disconnected, corners get counted twice in a quantity
    take, and the annotation shows a notch at every junction. So where two
    centrelines nearly cross, both are pulled to the crossing point.

    `reach_factor` scales how far an endpoint may travel, expressed in wall
    thicknesses — generous enough for a mitred corner, tight enough that two
    unrelated walls across the room never snap together.
    """
    adjusted = [
        WallRun(
            start=w.start,
            end=w.end,
            thickness_pts=w.thickness_pts,
            thickness_mm=w.thickness_mm,
            length_pts=w.length_pts,
            length_mm=w.length_mm,
        )
        for w in walls
    ]

    for i in range(len(adjusted)):
        for j in range(i + 1, len(adjusted)):
            a, b = adjusted[i], adjusted[j]

            crossing = _line_intersection(a, b)
            if crossing is None:
                continue

            reach = max(a.thickness_pts, b.thickness_pts) * reach_factor

            moved_a = _pull_endpoint_to(a, crossing, reach)
            if not moved_a:
                continue

            moved_b = _pull_endpoint_to(b, crossing, reach)
            if not moved_b:
                # Undo the first move — a joint needs both sides to reach.
                _pull_endpoint_to(a, moved_a[1], float("inf"), which=moved_a[0])

    for wall in adjusted:
        length_pts = math.hypot(wall.end[0] - wall.start[0], wall.end[1] - wall.start[1])
        wall.length_pts = round(length_pts, 2)
        wall.length_mm = round(pts_to_mm(length_pts, scale_ratio), 1)

    return adjusted


def _pull_endpoint_to(
    wall: WallRun,
    target: tuple[float, float],
    reach: float,
    which: str | None = None,
) -> tuple[str, tuple[float, float]] | None:
    """
    Move whichever end of the wall is nearest `target` onto it.

    Returns the end that moved and where it was, so the caller can undo.
    """
    snapped = (round(target[0], 2), round(target[1], 2))

    if which is None:
        to_start = math.hypot(wall.start[0] - target[0], wall.start[1] - target[1])
        to_end = math.hypot(wall.end[0] - target[0], wall.end[1] - target[1])
        which = "start" if to_start <= to_end else "end"
        if min(to_start, to_end) > reach:
            return None

    previous = getattr(wall, which)
    setattr(wall, which, snapped)

    return (which, previous)


def build_walls(
    segments: list[dict],
    *,
    scale_ratio: float = 100.0,
    include_colors: list[list[float]] | None = None,
    exclude_colors: list[list[float]] | None = None,
    min_stroke: float = 0.0,
    max_stroke: float | None = None,
    node_tolerance_pts: float = DEFAULT_NODE_TOLERANCE_PTS,
) -> tuple[list[Node], list[WallRun]]:
    """
    Full wall reconstruction: select faces, pair them, merge runs, snap nodes.

    Thickness bounds are derived from the drawing scale, so the same millimetre
    range works on a 1:50 sheet and a 1:200 one.
    """
    faces = select_wall_face_segments(
        segments,
        include_colors=include_colors,
        exclude_colors=exclude_colors,
        min_stroke=min_stroke,
        max_stroke=max_stroke,
    )

    walls = pair_wall_faces(
        faces,
        min_thickness_pts=mm_to_pts(MIN_WALL_THICKNESS_MM, scale_ratio),
        max_thickness_pts=mm_to_pts(MAX_WALL_THICKNESS_MM, scale_ratio),
        scale_ratio=scale_ratio,
    )
    walls = merge_collinear(walls, tolerance_pts=node_tolerance_pts, scale_ratio=scale_ratio)
    walls = connect_joints(walls, scale_ratio=scale_ratio)

    endpoints = [w.start for w in walls] + [w.end for w in walls]
    nodes, index_map = points_to_nodes(endpoints, tolerance=node_tolerance_pts)

    for wall in walls:
        wall.start_node = index_map[wall.start]
        wall.end_node = index_map[wall.end]

    return nodes, walls
