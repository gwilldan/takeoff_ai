"""
Setting-out grid detection.

Construction drawings carry their own reference marks — bubbles lettered across
the top and numbered down the side. Reading them means a wall can be described
as "A/1 to C/1" instead of a pair of raw coordinates, which is how engineers
actually talk about a plan and what keeps a takeoff checkable across revisions.

Detection is deterministic: no model is involved. A grid mark is a one or two
character alphanumeric label, and the marks for one axis all share a coordinate
— column marks sit in a horizontal band, row marks in a vertical one. Requiring
at least three aligned marks is what separates a real grid from a stray letter,
and the length limit is what keeps 3-5 digit dimension strings out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# One or two characters, letters or digits. Long enough for "12", too short for
# a dimension like "3600".
GRID_LABEL = re.compile(r"^[A-Za-z0-9]{1,2}$")

# Marks within this many points of each other count as sharing a band.
BAND_TOLERANCE_PTS = 4.0

# Below this many aligned marks it is not a grid.
MIN_MARKS_PER_AXIS = 3

# How close a point must be to an axis to be called "on" it.
DEFAULT_REF_TOLERANCE_PTS = 10.0


@dataclass
class GridAxis:
    ref: str
    position: float


@dataclass
class DrawingGrid:
    columns: list[GridAxis] = field(default_factory=list)
    rows: list[GridAxis] = field(default_factory=list)

    @property
    def is_present(self) -> bool:
        return bool(self.columns or self.rows)

    def to_dict(self) -> dict:
        return {
            "columns": [{"ref": a.ref, "x": a.position} for a in self.columns],
            "rows": [{"ref": a.ref, "y": a.position} for a in self.rows],
        }


def _grid_label_spans(cache) -> list[dict]:
    return [s for s in cache.text_spans if GRID_LABEL.match(s["text"].strip())]


def _cluster_by(spans: list[dict], key: str, tolerance: float) -> list[list[dict]]:
    """Group spans whose `key` coordinate falls within tolerance of each other."""
    clusters: list[list[dict]] = []

    for span in sorted(spans, key=lambda s: s[key]):
        if clusters and abs(span[key] - clusters[-1][-1][key]) <= tolerance:
            clusters[-1].append(span)
        else:
            clusters.append([span])

    return clusters


def _axes_from_band(band: list[dict], across_key: str) -> list[GridAxis]:
    """
    Turn one band of marks into axes, de-duplicating repeated references.

    A reference usually appears twice — once at each edge of the sheet — so the
    same label is merged and its position averaged.
    """
    by_ref: dict[str, list[float]] = {}

    for span in band:
        ref = span["text"].strip().upper()
        by_ref.setdefault(ref, []).append(span[across_key])

    axes = [
        GridAxis(ref=ref, position=round(sum(values) / len(values), 1))
        for ref, values in by_ref.items()
    ]

    return sorted(axes, key=lambda a: a.position)


def _best_axis_set(spans: list[dict], band_key: str, across_key: str) -> list[GridAxis]:
    """
    Find the strongest band of aligned marks.

    `band_key` is the coordinate the marks share (y for a row of column marks),
    `across_key` the one that varies along the axis.
    """
    best: list[GridAxis] = []

    for band in _cluster_by(spans, band_key, BAND_TOLERANCE_PTS):
        axes = _axes_from_band(band, across_key)
        if len(axes) >= MIN_MARKS_PER_AXIS and len(axes) > len(best):
            best = axes

    return best


def detect_grid(cache) -> DrawingGrid:
    """Read the drawing's own column and row reference marks."""
    spans = _grid_label_spans(cache)

    if len(spans) < MIN_MARKS_PER_AXIS:
        return DrawingGrid()

    # Column marks share a y and vary in x; row marks the other way round.
    columns = _best_axis_set(spans, band_key="cy", across_key="cx")
    rows = _best_axis_set(spans, band_key="cx", across_key="cy")

    # A single band cannot be both axes. Whichever has more marks wins, so a
    # lone row of letters is not also reported as a column of rows.
    if columns and rows and {a.ref for a in columns} == {a.ref for a in rows}:
        if len(columns) >= len(rows):
            rows = []
        else:
            columns = []

    return DrawingGrid(columns=columns, rows=rows)


def _axis_ref(axes: list[GridAxis], value: float, tolerance: float) -> str | None:
    """Nearest axis reference, or a bracketing pair when the point is between."""
    if not axes:
        return None

    nearest = min(axes, key=lambda a: abs(a.position - value))
    if abs(nearest.position - value) <= tolerance:
        return nearest.ref

    before = [a for a in axes if a.position < value]
    after = [a for a in axes if a.position > value]

    if before and after:
        return f"{before[-1].ref}~{after[0].ref}"

    return nearest.ref


def grid_ref_for_point(
    grid: DrawingGrid,
    x: float,
    y: float,
    tolerance_pts: float = DEFAULT_REF_TOLERANCE_PTS,
) -> str | None:
    """
    Describe a point by the drawing's grid, e.g. "A/1", or "A~B/1~2" when it
    falls between axes. Returns None when the drawing has no detectable grid.
    """
    column = _axis_ref(grid.columns, x, tolerance_pts)
    row = _axis_ref(grid.rows, y, tolerance_pts)

    if column and row:
        return f"{column}/{row}"

    return column or row
