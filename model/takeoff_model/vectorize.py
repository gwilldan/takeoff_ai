"""
Per-pixel class probabilities → takeoff detections, the way QuantTakeoff
reports them: one rectangle per straight wall run, one per door and window
opening, one polygon per room, column and stair, each with a score from the
model's probabilities.

Everything is built on what the network predicts, in the order a person reads
a plan:

1. Walls. The predicted wall mask is split into straight runs: opening it
   with a long horizontal line keeps the horizontal runs, a vertical line the
   vertical ones, and what is left (splays, curves) is traced as it is.
2. Openings are gaps in those walls. From the end of every run the wall band
   is followed until wall starts again; a gap of door-to-window width is a
   candidate opening, as wide as the gap and as thick as the wall. The network
   decides what it is: what it labels in the gap, and beside it where a
   door's swing is drawn, makes it a door, a window, or a plain opening that
   is not reported.
3. Rooms are the spaces the walls enclose. Walls, columns and openings form a
   barrier; every region it closes off, not reached from the page edge, is a
   candidate room, kept when the network calls most of it room. So a room
   stops at wall faces and never runs out into the margin through a gap.
"""

from __future__ import annotations

from typing import Any, Iterator

import cv2
import numpy as np


def _components(mask: np.ndarray, min_area: float, connectivity: int = 8) -> Iterator[tuple[int, int, np.ndarray]]:
    """(x, y, component mask cropped to its box) for each component at least `min_area` px."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=connectivity)
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if area >= min_area:
            yield int(x), int(y), (labels[y:y + h, x:x + w] == i)


def _score(probs: np.ndarray, index: int, x: int, y: int, part: np.ndarray) -> float:
    h, w = part.shape
    return round(float(probs[y:y + h, x:x + w, index][part].astype(np.float32).mean()), 4)


def _box(x: int, y: int, part: np.ndarray) -> list[list[float]]:
    """The component's pixel box, corners on its first and last pixels as the labels are drawn."""
    h, w = part.shape
    return [[x, y], [x + w - 1, y], [x + w - 1, y + h - 1], [x, y + h - 1]]


def _rect(x: int, y: int, part: np.ndarray) -> list[list[float]]:
    """Its pixel box when the component fills it (square-on), else the tightest rotated rectangle."""
    if part.mean() >= 0.9:
        return _box(x, y, part)
    box = cv2.boxPoints(cv2.minAreaRect(cv2.findNonZero(part.astype(np.uint8))))
    return [[round(float(px) + x, 1), round(float(py) + y, 1)] for px, py in box]


def _outline(x: int, y: int, part: np.ndarray, epsilon: float = 1.5) -> list[list[float]]:
    contours, _ = cv2.findContours(part.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = cv2.approxPolyDP(max(contours, key=cv2.contourArea), epsilon, True)
    return [[float(px + x), float(py + y)] for px, py in contour[:, 0]]


def _fill_holes(part: np.ndarray) -> np.ndarray:
    """A component with everything it encloses: furniture, text and free-standing columns become room."""
    padded = np.pad(part.astype(np.uint8), 1)
    outside = padded.copy()
    cv2.floodFill(outside, None, (0, 0), 1)
    return (padded | (1 - outside)).astype(bool)[1:-1, 1:-1]


def _band(x: int, y: int, part: np.ndarray) -> list[list[float]]:
    """A wall run: its box when the run fills it, else its traced outline (a stepped run)."""
    return _box(x, y, part) if part.mean() >= 0.75 else _outline(x, y, part)


def _wall_runs(wall: np.ndarray, run: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The wall mask as horizontal runs, vertical runs, and the rest."""
    run |= 1  # odd, so opening does not shift the runs by a pixel
    horizontal = cv2.morphologyEx(wall, cv2.MORPH_OPEN, np.ones((1, run), np.uint8))
    vertical = cv2.morphologyEx(wall, cv2.MORPH_OPEN, np.ones((run, 1), np.uint8))
    rest = wall & (1 - cv2.dilate(horizontal | vertical, np.ones((3, 3), np.uint8)))
    return horizontal, vertical, rest


def _gaps(runs: np.ndarray, solid: np.ndarray, min_run: int, shortest: int, longest: int) -> list[tuple[int, int, int, int]]:
    """
    Gaps along horizontal runs, as (x0, y0, x1, y1) inclusive boxes. From each
    end of a run the band of rows it occupies there is followed until wall
    (`solid`) covers most of it again, at most `longest` px away.
    """
    found = []
    width = solid.shape[1]
    for x, y, part in _components(runs, min_run):
        for end, step in ((part.shape[1] - 1, 1), (0, -1)):
            rows = np.flatnonzero(part[:, end])
            r0, r1 = y + rows[0], y + rows[-1]
            thick, column = r1 - r0 + 1, x + end
            above, below = solid[max(0, r0 - thick):r0, column], solid[r1 + 1:r1 + 1 + thick, column]
            if any(side.size == thick and side.mean() >= 0.9 for side in (above, below)):
                continue  # the run ends on a crossing wall: beyond it is the next room, not an opening
            start = x + end + step
            stop = min(width, start + longest) if step > 0 else max(-1, start - longest)
            if not 0 <= start < width:
                continue
            band = solid[r0:r1 + 1, start:stop:step] if step > 0 else solid[r0:r1 + 1, stop + 1:start + 1][:, ::-1]
            covered = np.flatnonzero(band.mean(axis=0) >= 0.5)
            if covered.size and covered[0] >= shortest:
                length = int(covered[0])
                x0, x1 = (start, start + length - 1) if step > 0 else (start - length + 1, start)
                found.append((x0, int(r0), x1, int(r1)))
    unique: list[tuple[int, int, int, int]] = []
    for g in found:  # each gap is found from both its ends
        if not any(abs(g[0] - u[0]) <= 2 and abs(g[2] - u[2]) <= 2 and g[1] <= u[3] and u[1] <= g[3] for u in unique):
            unique.append(g)
    return unique


def _openings(horizontal: np.ndarray, vertical: np.ndarray, solid: np.ndarray, label: np.ndarray, index: dict[str, int],
              settings: dict[str, Any], px: float) -> list[tuple[str | None, tuple[int, int, int, int], float]]:
    """
    Every gap in a wall, with what the network says fills it: ("door" or
    "window", box, evidence) or (None, box, evidence) for a plain opening.

    Evidence is how many pixels the network labels door or window per pixel of
    the gap. A window is looked for in the gap itself; a door also beside it,
    as far as a leaf reaches, since its swing is drawn there. Doors are at most
    `max_door_mm` wide.
    """
    shortest, longest = max(2, round(settings["min_opening_mm"] / px)), round(settings["max_opening_mm"] / px)
    widest_door, leaf = settings["max_door_mm"] / px, round(settings["max_door_mm"] / 2 / px)
    min_run = max(3, round(settings["min_wall_run_mm"] / px))
    out = []
    for transpose in (False, True):
        runs, wall, labels = (vertical.T, solid.T, label.T) if transpose else (horizontal, solid, label)
        for x0, y0, x1, y1 in _gaps(runs, wall, min_run, shortest, longest):
            length, area = x1 - x0 + 1, (x1 - x0 + 1) * (y1 - y0 + 1)
            evidence = {}
            if "window" in index:
                evidence["window"] = float((labels[y0:y1 + 1, x0:x1 + 1] == index["window"]).sum()) / area
            if "door" in index and length <= widest_door:
                reach = min(length, leaf * 2)
                around = labels[max(0, y0 - reach):y1 + reach + 1, x0:x1 + 1]
                evidence["door"] = float((around == index["door"]).sum()) / area
            kind = max(evidence, key=evidence.get) if evidence else None
            box = (y0, x0, y1, x1) if transpose else (x0, y0, x1, y1)
            if kind and evidence[kind] >= settings["opening_evidence"]:
                out.append((kind, box, min(1.0, evidence[kind])))
            else:
                out.append((None, box, max(evidence.values(), default=0.0)))
    return out


def _rooms(barrier: np.ndarray, probs: np.ndarray, space: int, min_area: float, min_space: float):
    """Regions the barrier closes off from the page edge that the network calls room, as (x, y, filled part, score)."""
    for x, y, part in _components(1 - barrier, min_area, connectivity=4):
        h, w = part.shape
        if x == 0 or y == 0 or x + w == barrier.shape[1] or y + h == barrier.shape[0]:
            continue  # reaches the edge of the drawing: outside the building
        score = float(probs[y:y + h, x:x + w, space][part].astype(np.float32).mean())
        if score >= min_space:
            yield x, y, _fill_holes(part), round(score, 4)


def vectorize(probs: np.ndarray, classes: list[str], mm_per_px: float, real_scale: bool,
              settings: dict[str, Any], page_index: int) -> list[dict[str, Any]]:
    """
    Detections for one page or drawing. `probs` is (height, width, classes);
    `mm_per_px` converts the millimetre settings to pixels; `real_scale` says
    whether it is the drawing's true scale, so areas can be reported.
    """
    label = probs.argmax(axis=-1).astype(np.uint8)
    index = {name: i for i, name in enumerate(classes)}
    px = mm_per_px
    px2_per_mm2 = 1 / px**2
    found: list[tuple[str, list[list[float]], float, dict[str, Any]]] = []
    mask = {name: (label == i).astype(np.uint8) for name, i in index.items()}
    zero = np.zeros(label.shape, np.uint8)

    wall = mask.get("wall", zero)
    solid = wall | mask.get("column", zero)
    run = max(3, round(settings["min_wall_run_mm"] / px))
    horizontal, vertical, rest = _wall_runs(wall, run)
    for part_mask in (horizontal, vertical):
        for x, y, part in _components(part_mask, run * 2):
            found.append(("wall", _band(x, y, part), _score(probs, index["wall"], x, y, part), {}))
    for x, y, part in _components(rest, run * 3):
        found.append(("wall", _rect(x, y, part) if part.mean() >= 0.5 else _outline(x, y, part),
                      _score(probs, index["wall"], x, y, part), {}))

    barrier = solid.copy()
    seal = settings["seal_gap_mm"] / px
    for kind, (x0, y0, x1, y1), evidence in _openings(horizontal, vertical, solid, label, index, settings, px):
        if kind:
            found.append((kind, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], round(evidence, 4), {}))
        if kind or max(x1 - x0, y1 - y0) + 1 <= seal:
            barrier[y0:y1 + 1, x0:x1 + 1] = 1
    close = max(1, round(settings["seal_break_mm"] / px)) | 1  # odd, so it stays centred
    barrier = cv2.morphologyEx(barrier, cv2.MORPH_CLOSE, np.ones((close, close), np.uint8))

    if "space" in index:
        for x, y, whole, score in _rooms(barrier, probs, index["space"], settings["min_room_m2"] * 1e6 * px2_per_mm2,
                                         settings["room_min_probability"]):
            extra: dict[str, Any] = {}
            if real_scale:
                contour = cv2.findContours(whole.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
                extra = {"area_m2": round(float(whole.sum()) * px**2 / 1e6, 2),
                         "perimeter_m": round(cv2.arcLength(max(contour, key=cv2.contourArea), True) * px / 1000, 2)}
            found.append(("space", _outline(x, y, whole, 2.0), score, extra))

    if "column" in index:
        columns = cv2.morphologyEx(mask["column"], cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        for x, y, part in _components(columns, settings["min_column_mm2"] * px2_per_mm2):
            found.append(("column", _rect(x, y, part), _score(probs, index["column"], x, y, part), {}))

    if "stair" in index:
        for x, y, part in _components(mask["stair"], settings["min_stair_m2"] * 1e6 * px2_per_mm2):
            found.append(("stair", _outline(x, y, _fill_holes(part), 2.0), _score(probs, index["stair"], x, y, part), {}))

    detections = []
    for n, (name, polygon, score, extra) in enumerate(found):
        xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
        detections.append({
            "id": f"det_{page_index}_{n:03d}", "class_name": name, "score": score,
            "bbox": [min(xs), min(ys), max(xs), max(ys)], "polygon": polygon,
            "coordinate_space": "page_pixels", **extra,
        })
    return detections
