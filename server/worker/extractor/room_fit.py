"""
Fit room outlines to the drawing's setting-out grid.

A room drawn as a square centred on its tag is right in area and wrong in shape.
The grid does better: its lines are real, drawn by the engineer, and internal
partitions usually sit on or near them. So the room's outline is chosen from the
rectangles the grid lines form.

Which rectangle? The one whose area matches the area the drawing prints. That is
the trick — the grid proposes candidates and the drawing's own annotation picks
the winner, so no threshold has to be guessed. Two constraints prune the search:
a room's rectangle must contain its own tag, and must not contain any other
room's tag.

Measured on a reference plan: 18 of 27 rooms land within 15% of the printed area,
median error 4%. The rest are small rooms in coarse regions of the grid, where no
grid rectangle is small enough — those keep the area-correct square, and say so.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

MM_PER_INCH = 25.4
PDF_POINTS_PER_INCH = 72

# Beyond this ratio a rectangle reads as a corridor, not a room. Past it, a mild
# penalty applies, so a sliver only wins when nothing squarer comes close on area.
SLIVER_ASPECT = 2.5
SLIVER_PENALTY = 0.15

# A grid fit worse than this is not an improvement on the area-correct square.
MAX_AREA_ERROR = 0.2


@dataclass
class RoomFit:
    polyline: list[list[float]]
    area_error: float
    grid_ref: str


def _area_m2(width_pts: float, height_pts: float, scale_ratio: float) -> float:
    mm_per_pt = (MM_PER_INCH / PDF_POINTS_PER_INCH) * scale_ratio
    return (width_pts * mm_per_pt) * (height_pts * mm_per_pt) / 1_000_000


def _edges(positions: list[float], pad: float) -> list[float]:
    """Grid lines, plus an outer edge so rooms on the perimeter can close."""
    ordered = sorted(set(positions))
    if not ordered:
        return []
    return [ordered[0] - pad, *ordered, ordered[-1] + pad]


def _outer_pad(positions: list[float]) -> float:
    """Half the median bay — enough for a perimeter room, too little to swallow one."""
    ordered = sorted(set(positions))
    if len(ordered) < 2:
        return 20.0
    gaps = sorted(b - a for a, b in zip(ordered, ordered[1:]))
    return gaps[len(gaps) // 2] / 2


def _ref_at(positions: dict[float, str], value: float) -> str:
    return positions.get(value, "·")


def fit_room_to_grid(
    tag: tuple[float, float],
    printed_area_m2: float,
    other_tags: list[tuple[float, float]],
    grid,
    scale_ratio: float,
) -> RoomFit | None:
    """
    Best grid rectangle for one room, or None when the grid cannot express it.

    Returns None rather than a poor fit: a wrong outline that looks authoritative
    is worse than an honest placeholder.
    """
    if printed_area_m2 <= 0 or not grid.columns or not grid.rows:
        return None

    column_positions = [axis.position for axis in grid.columns]
    row_positions = [axis.position for axis in grid.rows]

    xs = _edges(column_positions, _outer_pad(column_positions))
    ys = _edges(row_positions, _outer_pad(row_positions))

    column_refs = {axis.position: axis.ref for axis in grid.columns}
    row_refs = {axis.position: axis.ref for axis in grid.rows}

    tag_x, tag_y = tag
    best: tuple[float, float, float, float, float, float] | None = None

    for x1, x2 in itertools.combinations(xs, 2):
        if not x1 < tag_x < x2:
            continue

        for y1, y2 in itertools.combinations(ys, 2):
            if not y1 < tag_y < y2:
                continue

            if any(x1 < ox < x2 and y1 < oy < y2 for ox, oy in other_tags):
                continue

            width, height = x2 - x1, y2 - y1
            area = _area_m2(width, height, scale_ratio)
            error = abs(area - printed_area_m2) / printed_area_m2

            longest, shortest = max(width, height), min(width, height)
            aspect = longest / shortest if shortest else float("inf")
            score = error + SLIVER_PENALTY * max(0.0, aspect - SLIVER_ASPECT)

            if best is None or score < best[0]:
                best = (score, error, x1, y1, x2, y2)

    if best is None or best[1] > MAX_AREA_ERROR:
        return None

    _, error, x1, y1, x2, y2 = best

    return RoomFit(
        polyline=[
            [round(x1, 2), round(y1, 2)],
            [round(x2, 2), round(y1, 2)],
            [round(x2, 2), round(y2, 2)],
            [round(x1, 2), round(y2, 2)],
        ],
        area_error=round(error, 3),
        grid_ref=(
            f"{_ref_at(column_refs, x1)}~{_ref_at(column_refs, x2)}"
            f"/{_ref_at(row_refs, y1)}~{_ref_at(row_refs, y2)}"
        ),
    )
