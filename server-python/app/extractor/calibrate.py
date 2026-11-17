"""
Check the drawing scale against the drawing's own dimensions.

A stated scale is only true for the sheet it was printed on. A scan, a
"fit to page" export or a phone photo of an A1 plan on A3 paper keeps the
words "1:100" and loses their meaning; a sheet may state no scale at all. One
wrong scale silently corrupts every quantity, so it is measured, not trusted.

Every dimension string (2400, 7,500, 3.25) labels a dimension line drawn
beside it, ending in ticks, arrows or witness lines. The line is found in
the rendered page, its length measured between its terminators, and
value ÷ length gives millimetres per point. Chained dimensions, grid lines
crossing the chain and odd placements make single readings noisy, so the
answer is the tightest cluster of readings, not any one of them.

Drawings are dimensioned in millimetres, centimetres or metres. The unit is
whichever makes the measured scale agree with the stated one; with no stated
scale, whichever makes it closest to a scale draughtsmen actually use.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.extractor.render import render_page_array

MM_PER_PT = 25.4 / 72

# Dimension strings: 2400, 7,500 (thousands), 3.25 or 3,25 (metres or cm).
DIMENSION = re.compile(r"^(\d{1,3}(?:,\d{3})+|\d{2,6}|\d{1,3}[.,]\d{1,3})$")
MAX_DIMENSIONS = 200

# Ink for dimension lines: anything with colour or darkness (min channel),
# so red and blue dimension lines count and pale room fills do not.
LINE_INK_MAX = 160
RENDER_PX_PER_PT = 2.0
MAX_PIXELS = 20_000_000

UNITS_MM = (1.0, 10.0, 1000.0)
STANDARD_SCALES = (5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 750, 1000, 1250, 1500, 2000, 2500)

# Readings within this share of each other are one cluster. How many must
# agree, and what share of all readings that is, depends on what the cluster
# is used for: confirming a stated scale needs least, supplying a missing one
# more, overruling a stated one most.
CLUSTER_TOLERANCE = 0.03
CONFIRM = (4, 0.15)
SUPPLY = (5, 0.3)
OVERRULE = (6, 0.4)
# A stated scale within this share of the measured one is confirmed.
CONFIRM_TOLERANCE = 0.06
# To overrule a stated scale the evidence has to be stronger.
OVERRULE_AGREEING = 6
OVERRULE_SHARE = 0.4


@dataclass
class Scale:
    ratio: float
    text: str
    confidence: str
    source: str  # "stated", "dimensions", or "assumed"
    dimensions_checked: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"text": self.text, "ratio": self.ratio, "confidence": self.confidence,
                "source": self.source, "dimensionsChecked": self.dimensions_checked}


def _value(text: str) -> float | None:
    if not (match := DIMENSION.match(text.strip())):
        return None
    raw = match.group(1)
    if re.fullmatch(r"\d{1,3}(,\d{3})+", raw):
        return float(raw.replace(",", ""))
    return float(raw.replace(",", "."))


def _run(line: np.ndarray, centre: int) -> tuple[int, int]:
    """Extent of the inked run through `centre` in a 1-D row (gaps already closed)."""
    gaps_right = np.flatnonzero(~line[centre:])
    gaps_left = np.flatnonzero(~line[:centre][::-1])
    right = centre + (gaps_right[0] - 1 if len(gaps_right) else len(line) - 1 - centre)
    left = centre - (gaps_left[0] + 1 if len(gaps_left) else centre) + 1
    return left, right


def _terminator(ink: np.ndarray, row: int, start: int, end: int, reach: int) -> int:
    """First column from `start` towards `end` where ink leaves the line sideways (tick, arrow, witness)."""
    step = 1 if end >= start else -1
    cols = np.arange(start, end + step, step)
    sideways = ink[max(0, row - reach):max(0, row - 2), cols].sum(axis=0) + ink[row + 3:row + reach + 1, cols].sum(axis=0)
    hits = np.flatnonzero(sideways >= 2)
    return int(cols[hits[0]]) if len(hits) else end


def _measure(ink: np.ndarray, rows: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float | None:
    """
    Length in pixels of the dimension line labelled by a horizontal text box.
    `rows` is `ink` with pixel gaps along each row closed.
    """
    height = max(2, y1 - y0)
    centre = (x0 + x1) // 2
    if not 0 <= centre < ink.shape[1]:
        return None
    best = None
    for row in range(max(0, y0 - 2 * height), min(ink.shape[0], y1 + 2 * height)):
        if y0 <= row <= y1 or not rows[row, centre]:
            continue  # the text's own rows, or no line under the text here
        left, right = _run(rows[row], centre)
        if right - left >= (x1 - x0) and (best is None or right - left > best[2] - best[1]):
            best = (row, left, right)
    if best is None:
        return None
    row, left, right = best
    reach = max(3, height)
    # Start just outside the text, so its own glyphs never read as a tick.
    a = _terminator(ink, row, max(left, x0 - 2), left, reach)
    b = _terminator(ink, row, min(right, x1 + 2), right, reach)
    return float(b - a) if b - a > (x1 - x0) else None


def _vector_length(segments: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> float | None:
    """
    Length in points of the vector dimension line labelled by a horizontal
    text box. `segments` is (n, 4) x1, y1, x2, y2 in the text's frame.

    The line is the nearest horizontal segment beside the text that runs
    under its centre; its ends are wherever another segment crosses it (a
    tick, arrow or witness line) or a collinear piece of the chain stops.
    """
    height, centre = max(1.0, y1 - y0), (x0 + x1) / 2
    sx1, sy1, sx2, sy2 = segments.T
    flat = np.abs(sy1 - sy2) <= 0.2
    # Beside the text: off its centre line (a box includes descender space,
    # so the line may fall inside it), within two text heights.
    offset = np.abs(sy1 - (y0 + y1) / 2)
    beside = (offset >= 0.25 * height) & (offset <= 2 * height)
    under = (np.minimum(sx1, sx2) < centre) & (np.maximum(sx1, sx2) > centre)
    lines = np.flatnonzero(flat & beside & under)
    if not len(lines):
        return None
    line_y = sy1[lines[np.argmin(np.abs(sy1[lines] - (y0 + y1) / 2))]]

    on_line = flat & (np.abs(sy1 - line_y) <= 0.3)
    stops = np.concatenate([sx1[on_line], sx2[on_line]])
    crossing = ~flat & (np.minimum(sy1, sy2) <= line_y + 0.5) & (np.maximum(sy1, sy2) >= line_y - 0.5)
    t = (line_y - sy1[crossing]) / np.where(sy2[crossing] == sy1[crossing], 1, sy2[crossing] - sy1[crossing])
    stops = np.concatenate([stops, sx1[crossing] + t.clip(0, 1) * (sx2[crossing] - sx1[crossing])])

    left, right = stops[stops <= x0 + 0.5], stops[stops >= x1 - 0.5]
    if not len(left) or not len(right):
        return None
    return float(right.min() - left.max())


def _readings(cache) -> list[float]:
    """Millimetres-per-point implied by each dimension string, in its own (unknown) unit."""
    spans = [(s, v) for s in cache.text_spans if (v := _value(s["text"]))][:MAX_DIMENSIONS]
    if not spans:
        return []
    if len(cache.line_segments) >= len(spans):
        return _vector_readings(cache, spans)
    return _pixel_readings(cache, spans)


def _vector_readings(cache, spans) -> list[float]:
    segments = np.array([[*l["start"], *l["end"]] for l in cache.line_segments], dtype=float)
    rotated = segments[:, [1, 0, 3, 2]]  # vertical text: swap x and y
    readings = []
    for span, value in spans:
        vertical = (span["y2"] - span["y"]) > 1.5 * (span["x2"] - span["x"])
        length = (_vector_length(rotated, span["y"], span["x"], span["y2"], span["x2"]) if vertical
                  else _vector_length(segments, span["x"], span["y"], span["x2"], span["y2"]))
        if length and length > 0:
            readings.append(value / length)
    return readings


def _pixel_readings(cache, spans) -> list[float]:
    px_per_pt = min(RENDER_PX_PER_PT, math.sqrt(MAX_PIXELS / (cache.page_width_pts * cache.page_height_pts)))
    ink = (render_page_array(cache.pdf_path, cache.page_index, px_per_pt, antialias=False).min(axis=2) < LINE_INK_MAX).astype(np.uint8)
    gap = np.ones((1, 5), np.uint8)
    rows = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, gap).astype(bool)
    columns = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, gap.T).astype(bool).T
    ink = ink.astype(bool)

    readings = []
    for span, value in spans:
        x0, y0, x1, y1 = (int(round(span[k] * px_per_pt)) for k in ("x", "y", "x2", "y2"))
        vertical = (y1 - y0) > 1.5 * (x1 - x0)
        length = _measure(ink.T, columns, y0, x0, y1, x1) if vertical else _measure(ink, rows, x0, y0, x1, y1)
        if length:
            readings.append(value / (length / px_per_pt))
    return readings


def _cluster(readings: list[float]) -> tuple[float, int]:
    """Centre and size of the largest cluster of readings."""
    values = np.array(readings)
    best = (0.0, 0)
    for value in values:
        members = values[np.abs(values - value) <= CLUSTER_TOLERANCE * value]
        if len(members) > best[1]:
            best = (float(np.median(members)), len(members))
    return best


def _enough(agreeing: int, total: int, rule: tuple[int, float]) -> bool:
    return agreeing >= rule[0] and agreeing >= rule[1] * total


def _nearest_standard(ratio: float) -> float:
    return min(STANDARD_SCALES, key=lambda s: abs(math.log(ratio / s)))


def check_scale(cache, profile: dict) -> Scale:
    said = profile.get("scale")
    stated = float(profile.get("scale_ratio") or 0) if said and said != "unknown" else None
    stated_text = said if stated else f"1:{stated:g}" if stated else None
    readings = _readings(cache)
    total = len(readings)
    per_pt, agreeing = _cluster(readings) if readings else (0.0, 0)
    candidates = [per_pt * unit / MM_PER_PT for unit in UNITS_MM]

    if stated and agreeing:
        measured = min(candidates, key=lambda r: abs(math.log(r / stated)))
        if abs(measured - stated) <= CONFIRM_TOLERANCE * stated and _enough(agreeing, total, CONFIRM):
            return Scale(stated, stated_text, "high", "stated", agreeing,
                         [f"Stated scale confirmed by {agreeing} of {total} printed dimensions"])
        if abs(measured - stated) > CONFIRM_TOLERANCE * stated and _enough(agreeing, total, OVERRULE):
            ratio = round(measured, 1)
            return Scale(ratio, f"1:{ratio:g}", "medium", "dimensions", agreeing, [
                f"The sheet says {stated_text} but {agreeing} of {total} printed dimensions measure 1:{ratio:g}; "
                "the sheet appears to have been resized, so the measured scale is used"])
    if stated:
        return Scale(stated, stated_text, profile.get("confidence") or "medium", "stated", 0,
                     [f"Stated scale could not be checked against the {total} dimension(s) measured"])

    if agreeing and _enough(agreeing, total, SUPPLY):
        # Millimetres unless centimetres or metres land clearly nearer a real scale.
        measured = min(candidates, key=lambda r: abs(math.log(r / _nearest_standard(r))) + (0 if r == candidates[0] else 0.05))
        ratio = round(measured, 1)
        return Scale(ratio, f"1:{ratio:g}", "medium", "dimensions", agreeing,
                     [f"No scale stated; measured 1:{ratio:g} from {agreeing} of {total} printed dimensions"])
    return Scale(100.0, "1:100", "low", "assumed", 0,
                 ["No scale stated and none measurable from the dimensions; 1:100 assumed — check before using quantities"])
