"""
Synthetic drawing sheets with exact labels, for pretraining and for testing the
pipeline end to end without any real drawings.

Each sheet looks like a real one, with everything that is not a plan's
structure there to be ignored:
- One or two buildings on a sheet, with L- and U-shaped footprints.
- Walls in one office's style: solid, hatched, cross-hatched, outlined, grey
  or coloured poché. Thin partitions sit beside blockwork.
- Doors that swing, double-swing or slide, and open archways. Windows are
  drawn in several ways, with sills.
- Rooms that are tinted or tiled, with furniture by room type: beds, sofas,
  tables, office desks, WCs, basins and kitchen counters. Names, areas,
  levels and stairs are drawn in the rooms.
- A setting-out grid, columns, dimension chains in tiers, door and window
  tags, section marks and dashed overhead lines.
- Outside the buildings: paving, trees and a site boundary.
- Around them: title block, notes, legend, schedule and north arrow, then
  scan noise.

Labels follow the takeoff format, in the image's pixels:
- Wall runs, stopping at openings.
- Door and window openings: the gap in the wall, as QuantTakeoff reports them.
- Rooms from wall face to wall face.
- Columns and stairs.

Archways, paving, furniture and everything on the sheet around the plans are
background.

    takeoff-synth --count 500             # into data/sources and data/labels
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from takeoff_model.config import load_config

DPI = 300
PAPER = DPI / 25.4  # px per millimetre of paper
SCALES = [50, 75, 100, 125, 150, 200]
GRID = 50  # mm: every room edge lies on it
FONT = cv2.FONT_HERSHEY_SIMPLEX

ROOMS = {  # name → furniture
    "BEDROOM": "bed", "MASTER BEDROOM": "bed", "GUEST ROOM": "bed", "KITCHEN": "kitchen", "PANTRY": "kitchen",
    "TOILET": "wet", "WC": "wet", "BATH": "wet", "SHOWER": "wet", "LAUNDRY": "wet", "LOBBY": "", "HALL": "",
    "CORRIDOR": "", "STORE": "", "DINING": "dining", "MEETING ROOM": "dining", "LOUNGE": "sitting",
    "SITTING ROOM": "sitting", "LIVING ROOM": "sitting", "OFFICE": "office", "POOL OFFICE": "office", "STUDY": "office",
}
OFFICE_ROOMS = ["OFFICE", "POOL OFFICE", "MEETING ROOM", "STORE", "TOILET", "CORRIDOR", "LOBBY", "KITCHEN"]
WORDS = ("ALL DIMENSIONS ARE IN MILLIMETRES UNLESS OTHERWISE STATED DO NOT SCALE FROM THIS DRAWING CONTRACTOR TO VERIFY "
         "ON SITE BLOCKWORK 225MM SANDCRETE RENDERED BOTH SIDES FLOOR FINISH TERRAZZO TILES REFER TO STRUCTURAL "
         "DRAWINGS FOR COLUMNS AND BEAMS WINDOWS ALUMINIUM CASEMENT DOORS FLUSH HARDWOOD").split()
TINTS = [(255, 250, 205), (235, 245, 225), (245, 238, 225), (230, 240, 250), (240, 240, 240)]


@dataclass
class Segment:
    """One wall between two rooms, or a room and the outside, in building millimetres."""
    horizontal: bool
    pos: float
    start: float
    end: float
    exterior: bool
    thickness: float = 0.0
    openings: list = field(default_factory=list)  # [start, end, kind, style]; kind door, window or opening


# --- drawing helpers, in pixels --------------------------------------------------

def _pt(p) -> tuple[int, int]:
    return int(round(p[0])), int(round(p[1]))


def _dashed(img, p, q, color, width, pattern):
    """A line drawn in `pattern`, a list of on/off lengths in px."""
    p, q = np.asarray(p, float), np.asarray(q, float)
    length = float(np.hypot(*(q - p)))
    if length < 1:
        return
    direction, at, k = (q - p) / length, 0.0, 0
    while at < length:
        step = pattern[k % len(pattern)]
        if k % 2 == 0:
            cv2.line(img, _pt(p + direction * at), _pt(p + direction * min(length, at + step)), color, width)
        at, k = at + step, k + 1


def _text(img, s, x, y, height, color, vertical=False, centre=True):
    """`s` about `height` px tall, centred on (x, y) unless `centre` is false; turned to read up the page when vertical."""
    scale = max(0.3, height / 22)
    thick = max(1, round(height / 14))
    (w, h), base = cv2.getTextSize(s, FONT, scale, thick)
    if not vertical:
        org = (int(x - w / 2), int(y + h / 2)) if centre else (int(x), int(y))
        cv2.putText(img, s, org, FONT, scale, color, thick, cv2.LINE_AA)
        return
    patch = np.full((h + base + 4, w + 4, 3), 255, np.uint8)
    cv2.putText(patch, s, (2, h + 2), FONT, scale, (0, 0, 0), thick, cv2.LINE_AA)
    patch = cv2.rotate(patch, cv2.ROTATE_90_COUNTERCLOCKWISE)
    ph, pw = patch.shape[:2]
    x0, y0 = int(x - pw / 2), int(y - ph / 2)
    if x0 < 0 or y0 < 0 or x0 + pw > img.shape[1] or y0 + ph > img.shape[0]:
        return
    ink = patch.min(axis=2) < 128
    img[y0:y0 + ph, x0:x0 + pw][ink] = color


def _hatch(img, pts, spacing, angle, color, width=1):
    """Parallel lines `spacing` px apart at `angle` degrees, clipped to polygon `pts`."""
    pts = np.asarray(pts, np.int32)
    x0, y0 = np.maximum(pts.min(0), 0)
    x1, y1 = np.minimum(pts.max(0), [img.shape[1] - 1, img.shape[0] - 1])
    if x1 <= x0 or y1 <= y0:
        return
    w, h = x1 - x0 + 1, y1 - y0 + 1
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [pts - [x0, y0]], 1)
    lines = np.zeros_like(mask)
    t = np.radians(angle)
    d, n = np.array([np.cos(t), np.sin(t)]), np.array([-np.sin(t), np.cos(t)])
    reach = w + h
    centre = np.array([w / 2, h / 2])
    for k in np.arange(-reach, reach, max(2.0, spacing)):
        a, b = centre + n * k - d * reach, centre + n * k + d * reach
        cv2.line(lines, _pt(a), _pt(b), 1, width)
    img[y0:y1 + 1, x0:x1 + 1][(lines & mask).astype(bool)] = color


def _rect_pts(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# --- one building ------------------------------------------------------------------

class Building:
    """A building's layout in millimetres: rooms on a 50 mm grid, the walls between them and their openings."""

    def __init__(self, rng: np.random.Generator, office: bool):
        self.rng = rng
        self.w, self.h = round(rng.uniform(7000, 22000), -2), round(rng.uniform(6000, 16000), -2)
        self.t_ext = float(rng.choice([200, 225, 230, 250, 300]))
        self.t_int = float(rng.choice([100, 115, 150, 225]))
        self.partition = float(rng.choice([75, 100])) if office or rng.random() < 0.2 else None
        rects = self.split((0, 0, self.w, self.h), 0)
        self.notches = []
        if len(rects) >= 5 and rng.random() < 0.5:  # L- and U-shaped footprints
            corners = [r for r in rects if (r[0] == 0 or r[2] == self.w) and (r[1] == 0 or r[3] == self.h)]
            for k in rng.permutation(len(corners))[:rng.integers(1, 3)]:
                if len(rects) - 1 >= 4:
                    rects.remove(corners[k])
                    self.notches.append(corners[k])
        names = OFFICE_ROOMS if office else list(ROOMS)
        self.rooms = [(r, str(rng.choice(names))) for r in rects]
        self.segments = self.walls()
        for seg in self.segments:
            seg.thickness = self.t_ext if seg.exterior else (self.partition if self.partition and rng.random() < 0.6 else self.t_int)
            seg.openings = self.openings(seg)

    def split(self, rect, depth, min_room=2400):
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        if depth > 4 or max(w, h) < 2 * min_room or (max(w, h) < 5500 and self.rng.random() < 0.45):
            return [rect]
        choices = [c for c, side in ((True, h), (False, w)) if side >= 2 * min_room]
        across = (h > w) if abs(h - w) > 1000 and len(choices) == 2 else bool(self.rng.choice(choices))
        lo, hi = (y0, y1) if across else (x0, x1)
        cut = round(self.rng.uniform(lo + min_room, hi - min_room) / GRID) * GRID
        a, b = ((x0, y0, x1, cut), (x0, cut, x1, y1)) if across else ((x0, y0, cut, y1), (cut, y0, x1, y1))
        return self.split(a, depth + 1) + self.split(b, depth + 1)

    def walls(self) -> list[Segment]:
        """A wall wherever two different rooms, or a room and the outside, meet: one segment per pair along a line."""
        nx, ny = int(self.w // GRID), int(self.h // GRID)
        cells = np.full((ny + 2, nx + 2), -1, int)
        for k, ((x0, y0, x1, y1), _) in enumerate(self.rooms):
            cells[1 + int(y0 // GRID):1 + int(y1 // GRID), 1 + int(x0 // GRID):1 + int(x1 // GRID)] = k
        segments = []
        for horizontal in (True, False):
            grid = cells if horizontal else cells.T
            for j in range(1, grid.shape[0]):
                pairs = [tuple(sorted((int(p), int(q)))) if p != q else None for p, q in zip(grid[j - 1], grid[j])]
                i = 0
                while i < len(pairs):
                    if pairs[i] is None:
                        i += 1
                        continue
                    k = i
                    while k + 1 < len(pairs) and pairs[k + 1] == pairs[i]:
                        k += 1
                    segments.append(Segment(horizontal, (j - 1) * GRID, (i - 1) * GRID, k * GRID, -1 in pairs[i]))
                    i = k + 1
        return segments

    def openings(self, seg: Segment) -> list:
        rng, found = self.rng, []
        lo, hi = seg.start + self.t_ext / 2 + 150, seg.end - self.t_ext / 2 - 150
        if seg.exterior:
            at = lo + rng.uniform(200, 900)
            while True:
                door = rng.random() < 0.15
                w = round(rng.uniform(900, 1200) if door else rng.uniform(600, 2400), -1)
                if at + w > hi:
                    break
                found.append([at, at + w, "door" if door else "window", str(rng.choice(["triple", "single", "sill"]))])
                at += w + rng.uniform(700, 3000)
        elif rng.random() < 0.85:
            double = hi - lo > 2400 and rng.random() < 0.15
            w = round(rng.uniform(1400, 1800) if double else rng.uniform(750, 950), -1)
            if hi - lo < w + 50:
                return found
            at = rng.uniform(lo, hi - w)
            kind = "opening" if rng.random() < 0.08 else "door"
            style = "double" if double else str(rng.choice(["swing"] * 8 + ["sliding"]))
            found.append([at, at + w, kind, style])
        return found


# --- a sheet -----------------------------------------------------------------------

class Sheet:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.labels: list[tuple[str, list[list[float]]]] = []
        office = rng.random() < 0.25
        self.buildings = [Building(rng, office) for _ in range(2 if rng.random() < 0.25 else 1)]
        span = max(max(b.w, b.h) for b in self.buildings)
        self.denominator = next((d for d in SCALES if span * DPI / 25.4 / d <= rng.uniform(1600, 2600)), 200)
        self.s = DPI / 25.4 / self.denominator  # px per real mm
        lw = rng.choice([0.7, 1.0, 1.0, 1.4])  # this office's pen weights
        self.thin, self.mid, self.thick = max(1, round(0.18 * PAPER * lw)), max(1, round(0.3 * PAPER * lw)), max(2, round(0.5 * PAPER * lw))
        self.coloured = rng.random() < 0.4
        self.ink = (0, 0, 0) if rng.random() < 0.7 else tuple(int(v) for v in rng.integers(0, 90, 3))
        self.wall_style = str(rng.choice(["solid", "hatch", "crosshatch", "outline", "grey", "poche"]))
        self.poche = tuple(int(v) for v in rng.choice([(200, 90, 70), (170, 170, 170), (120, 120, 200), (90, 160, 90)]))
        self.tinted = rng.random() < 0.25

    def px(self, mm):
        return mm * self.s

    def draw(self) -> np.ndarray:
        rng, s = self.rng, self.s
        pad = int(self.px(3200) + 90)  # dimension tiers, grid bubbles and a plan title
        sizes = [(int(self.px(b.w)) + 2 * pad, int(self.px(b.h)) + 2 * pad + 80) for b in self.buildings]
        strip = int(rng.uniform(0.16, 0.24) * sum(w for w, _ in sizes)) + 200
        width = sum(w for w, _ in sizes) + strip + 80
        height = max(max(h for _, h in sizes), 1400) + 80
        img = np.full((height, width, 3), 255, np.uint8)
        x = 40
        for building, (w, h) in zip(self.buildings, sizes):
            self.ox, self.oy = x + pad, 40 + pad + rng.uniform(0, max(0, height - 80 - h))
            self.building(img, building)
            x += w
        self.sheet(img, width - strip - 20, strip)
        return self.degrade(img)

    def to_px(self, x, y):
        return self.ox + x * self.s, self.oy + y * self.s

    def band(self, seg: Segment, a, b):
        """Pixel corners of the wall piece from a to b along `seg`, its full thickness."""
        t = seg.thickness
        if seg.horizontal:
            (x0, y0), (x1, y1) = self.to_px(a, seg.pos - t / 2), self.to_px(b, seg.pos + t / 2)
        else:
            (x0, y0), (x1, y1) = self.to_px(seg.pos - t / 2, a), self.to_px(seg.pos + t / 2, b)
        return _rect_pts(x0, y0, x1, y1)

    # --- a building ------------------------------------------------------------

    def building(self, img, b: Building):
        rng, ink = self.rng, self.ink
        blockers = []  # walls, openings and columns: rooms are what they leave
        self.outside(img, b)
        grid = self.grid(img, b)
        for rect, name in b.rooms:
            self.floor(img, rect, name)
        stair_room = None
        if rng.random() < 0.35:
            fits = [r for r in b.rooms if r[0][2] - r[0][0] >= 2600 and r[0][3] - r[0][1] >= 4300]
            if fits:
                stair_room = fits[rng.integers(len(fits))][0]
                self.stair(img, stair_room)
        for rect, name in b.rooms:
            if rect != stair_room:
                self.furniture(img, rect, ROOMS.get(name, ""))
        door_pen = (200, 0, 200) if self.coloured else ink
        window_pen = (0, 170, 220) if self.coloured else ink
        for seg in b.segments:
            a, e = seg.start - seg.thickness / 2, seg.end + seg.thickness / 2  # runs on to close corners and junctions
            edges = [a] + [v for o in seg.openings for v in o[:2]] + [e]
            for s0, s1 in zip(edges[::2], edges[1::2]):
                if s1 - s0 > 40:
                    corners = self.band(seg, s0, s1)
                    self.labels.append(("wall", corners))
                    blockers.append(corners)
                    self.wall(img, corners, seg.thickness < 120)
            for o0, o1, kind, style in seg.openings:
                corners = self.band(seg, o0, o1)
                blockers.append(corners)
                if kind != "opening":
                    self.labels.append((kind, corners))
                if kind == "window":
                    self.window(img, seg, o0, o1, style, window_pen)
                elif kind == "door":
                    self.door(img, seg, o0, o1, style, door_pen)
        blockers += self.columns(img, b, grid)
        for rect, name in b.rooms:
            self.room(img, rect, name, blockers)
        self.tags(img, b)
        self.dimensions(img, b, grid)
        if rng.random() < 0.4:
            self.section_mark(img, b)
        if rng.random() < 0.35:
            self.overhead(img, b)
        title = str(rng.choice(["GROUND FLOOR PLAN", "FIRST FLOOR PLAN", "FLOOR PLAN", "BASEMENT FLOOR PLAN", "TYPICAL FLOOR PLAN"]))
        tx, ty = self.to_px(0, b.h)
        ty += self.px(3200) + 30
        _text(img, title, tx, ty, 5 * PAPER, ink, centre=False)
        _text(img, f"SCALE 1:{self.denominator}", tx, ty + 7 * PAPER, 2.5 * PAPER, ink, centre=False)

    def outside(self, img, b: Building):
        rng = self.rng
        gray = (150, 150, 150)
        for x0, y0, x1, y1 in b.notches:  # paved courtyards and porches: outside, not rooms
            (a, c), (d, e) = self.to_px(x0, y0), self.to_px(x1, y1)
            pts = _rect_pts(a, c, d, e)
            if rng.random() < 0.6:
                _hatch(img, pts, self.px(rng.choice([300, 400, 600])), rng.choice([0, 45]), gray)
                if rng.random() < 0.5:
                    _hatch(img, pts, self.px(400), 90, gray)
            cv2.polylines(img, [np.array(pts, np.int32)], True, self.ink, self.thin)
        if rng.random() < 0.4:
            m = rng.uniform(1500, 2800)
            (a, c), (d, e) = self.to_px(-m, -m), self.to_px(b.w + m, b.h + m)
            pts = [_pt((a, c)), _pt((d, c)), _pt((d, e)), _pt((a, e))]
            for p, q in zip(pts, pts[1:] + pts[:1]):
                _dashed(img, p, q, gray, self.thin, [self.px(600), self.px(150), self.px(80), self.px(150)])
        for _ in range(rng.integers(0, 6)):
            side = rng.integers(4)
            x = rng.uniform(-2500, b.w + 2500) if side < 2 else (-rng.uniform(800, 2400) if side == 2 else b.w + rng.uniform(800, 2400))
            y = rng.uniform(-2500, b.h + 2500) if side >= 2 else (-rng.uniform(800, 2400) if side == 0 else b.h + rng.uniform(800, 2400))
            c, r = _pt(self.to_px(x, y)), int(self.px(rng.uniform(500, 1200)))
            cv2.circle(img, c, r, (90, 150, 90) if self.coloured else gray, self.thin)
            for k in range(6):
                t = k * np.pi / 3
                cv2.line(img, c, _pt((c[0] + r * 0.7 * np.cos(t), c[1] + r * 0.7 * np.sin(t))), gray, 1)

    def grid(self, img, b: Building) -> tuple[list[float], list[float]]:
        """Setting-out axes on some wall lines, drawn or not, and returned for columns and dimensions."""
        rng = self.rng
        xs = sorted({s.pos for s in b.segments if not s.horizontal})
        ys = sorted({s.pos for s in b.segments if s.horizontal})
        xs = [x for x in xs if x in (0, b.w) or rng.random() < 0.6]
        ys = [y for y in ys if y in (0, b.h) or rng.random() < 0.6]
        if rng.random() < 0.35:
            return xs, ys
        pen = (60, 60, 200) if self.coloured else (110, 110, 110)
        r = int(4 * PAPER)
        pattern = [self.px(800), self.px(150), self.px(100), self.px(150)]
        for vertical, axes, letters in ((True, xs, [chr(65 + i % 26) for i in range(len(xs))]), (False, ys, [str(i + 1) for i in range(len(ys))])):
            for v, name in zip(axes, letters):
                if vertical:
                    p, q = self.to_px(v, -2900), self.to_px(v, b.h + 2900)
                    heads = [(p[0], p[1] - r), (q[0], q[1] + r)]
                else:
                    p, q = self.to_px(-2900, v), self.to_px(b.w + 2900, v)
                    heads = [(p[0] - r, p[1]), (q[0] + r, q[1])]
                _dashed(img, p, q, pen, self.thin, pattern)
                for h in heads[:rng.integers(1, 3)]:
                    cv2.circle(img, _pt(h), r, pen, self.thin)
                    _text(img, name, h[0], h[1], r * 0.9, pen)
        return xs, ys

    def floor(self, img, rect, name):
        rng = self.rng
        (a, c), (d, e) = self.to_px(rect[0], rect[1]), self.to_px(rect[2], rect[3])
        pts = _rect_pts(a, c, d, e)
        if self.tinted:
            cv2.fillPoly(img, [np.array(pts, np.int32)], TINTS[rng.integers(len(TINTS))])
        if ROOMS.get(name) == "wet" and rng.random() < 0.7 or rng.random() < 0.1:
            tile = self.px(rng.choice([300, 400, 600]))
            for angle in (0, 90) if rng.random() < 0.8 else (45,):
                _hatch(img, pts, tile, angle, (170, 170, 170))

    def wall(self, img, corners, partition):
        pts = np.array(corners, np.int32)
        ink, style = self.ink, ("outline" if partition and self.rng.random() < 0.5 else self.wall_style)
        if style == "solid":
            cv2.fillPoly(img, [pts], ink)
        elif style == "grey":
            cv2.fillPoly(img, [pts], (150, 150, 150))
            cv2.polylines(img, [pts], True, ink, self.thin)
        elif style == "poche":
            cv2.fillPoly(img, [pts], self.poche)
            cv2.polylines(img, [pts], True, ink, self.thin)
        elif style in ("hatch", "crosshatch"):
            cv2.fillPoly(img, [pts], (255, 255, 255))
            _hatch(img, pts, max(4, self.px(60)), 45, ink)
            if style == "crosshatch":
                _hatch(img, pts, max(4, self.px(60)), -45, ink)
            cv2.polylines(img, [pts], True, ink, self.mid)
        else:
            cv2.fillPoly(img, [pts], (255, 255, 255))
            cv2.polylines(img, [pts], True, ink, self.mid)

    def window(self, img, seg: Segment, o0, o1, style, pen):
        corners = self.band(seg, o0, o1)
        pts = np.array(corners, np.int32)
        cv2.fillPoly(img, [pts], (255, 255, 255))
        (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
        along = [0.5] if style == "single" else [0.4, 0.6]
        for f in along:
            if seg.horizontal:
                cv2.line(img, (x0, int(y0 + f * (y1 - y0))), (x1, int(y0 + f * (y1 - y0))), pen, self.thin)
            else:
                cv2.line(img, (int(x0 + f * (x1 - x0)), y0), (int(x0 + f * (x1 - x0)), y1), pen, self.thin)
        cv2.polylines(img, [pts], True, pen, self.thin)
        if style == "sill":  # the sill stands proud of the wall and past the opening: not the window
            t = seg.thickness / 2
            for side in (-1, 1):
                for off in (t + 60,):
                    if seg.horizontal:
                        p, q = self.to_px(o0 - 100, seg.pos + side * off), self.to_px(o1 + 100, seg.pos + side * off)
                    else:
                        p, q = self.to_px(seg.pos + side * off, o0 - 100), self.to_px(seg.pos + side * off, o1 + 100)
                    if side == 1 or self.rng.random() < 0.5:
                        cv2.line(img, _pt(p), _pt(q), pen, self.thin)

    def door(self, img, seg: Segment, o0, o1, style, pen):
        rng, t = self.rng, seg.thickness
        if style == "sliding":
            for k, (a, b) in enumerate(((o0, o0 + 0.55 * (o1 - o0)), (o1 - 0.55 * (o1 - o0), o1))):
                off = (k - 0.5) * t * 0.35
                corners = self.band(Segment(seg.horizontal, seg.pos + off, 0, 0, False, t * 0.2), a, b)
                cv2.polylines(img, [np.array(corners, np.int32)], True, pen, self.thin)
            return
        side = rng.choice([-1, 1])
        leaves = [(o0, o1, 1)] if style != "double" else [(o0, (o0 + o1) / 2, 1), (o1, (o0 + o1) / 2, -1)]
        for hinge_at, free_at, _ in leaves:
            w = abs(free_at - hinge_at)
            face = seg.pos + side * t / 2
            if seg.horizontal:
                hinge, leaf, jamb = self.to_px(hinge_at, face), self.to_px(hinge_at, face + side * w), self.to_px(free_at, face)
            else:
                hinge, leaf, jamb = self.to_px(face, hinge_at), self.to_px(face + side * w, hinge_at), self.to_px(face, free_at)
            hinge, leaf, jamb = _pt(hinge), _pt(leaf), _pt(jamb)
            cv2.line(img, hinge, leaf, pen, self.mid)
            r = int(self.px(w))
            start = np.degrees(np.arctan2(leaf[1] - hinge[1], leaf[0] - hinge[0]))
            end = np.degrees(np.arctan2(jamb[1] - hinge[1], jamb[0] - hinge[0]))
            sweep = (end - start + 540) % 360 - 180
            if rng.random() < 0.2:
                for k in range(0, 18, 2):
                    cv2.ellipse(img, hinge, (r, r), 0, start + sweep * k / 18, start + sweep * (k + 1) / 18, pen, self.thin)
            else:
                cv2.ellipse(img, hinge, (r, r), 0, start, start + sweep, pen, self.thin)

    def stair(self, img, rect):
        rng, ink = self.rng, self.ink
        w, h = rng.uniform(1000, 1300), rng.uniform(2400, 3600)
        x, y = rect[0] + rng.uniform(300, rect[2] - rect[0] - w - 300), rect[1] + rng.uniform(300, rect[3] - rect[1] - h - 300)
        (a, c), (d, e) = self.to_px(x, y), self.to_px(x + w, y + h)
        self.labels.append(("stair", _rect_pts(a, c, d, e)))
        cv2.rectangle(img, _pt((a, c)), _pt((d, e)), ink, self.mid)
        tread = self.px(rng.choice([250, 275, 300]))
        yy = c + tread
        while yy < e - 1:
            cv2.line(img, _pt((a, yy)), _pt((d, yy)), ink, self.thin)
            yy += tread
        mid = (a + d) / 2
        cv2.arrowedLine(img, _pt((mid, e - tread / 2)), _pt((mid, c + tread)), ink, self.thin, tipLength=0.05)
        _text(img, "UP", mid + self.px(200), e - tread, 2 * PAPER, ink)
        if rng.random() < 0.5:
            cv2.line(img, _pt((a, c + (e - c) * 0.6)), _pt((d, c + (e - c) * 0.45)), ink, self.thin)

    def furniture(self, img, rect, kind):
        rng = self.rng
        pen = (0, 190, 230) if self.coloured and rng.random() < 0.5 else (90, 90, 90)
        x0, y0, x1, y1 = rect
        items = {"bed": [(1500, 2000, "bed"), (600, 1800, "wardrobe"), (450, 450, "box")],
                 "sitting": [(2200, 850, "sofa"), (1100, 600, "box"), (800, 800, "chair"), (800, 800, "chair")],
                 "dining": [(1800, 900, "table")], "office": [(1400, 700, "desk")] * int(rng.integers(2, 8)),
                 "wet": [(400, 700, "wc"), (550, 450, "basin"), (1700, 750, "bath")],
                 "kitchen": [(x1 - x0 - 400, 600, "counter"), (600, 600, "stove")]}.get(kind, [(800, 800, "box")] * int(rng.integers(0, 3)))
        placed = []
        for w, h, what in items:
            w, h = (h, w) if rng.random() < 0.3 and what not in ("counter",) else (w, h)
            if x1 - x0 < w + 600 or y1 - y0 < h + 600:
                continue
            for _ in range(8):
                fx = x0 + 300 if what in ("counter", "wardrobe") else rng.uniform(x0 + 300, x1 - w - 300)
                fy = rng.uniform(y0 + 300, y1 - h - 300)
                box = (fx, fy, fx + w, fy + h)
                if all(box[2] < p[0] or p[2] < box[0] or box[3] < p[1] or p[3] < box[1] for p in placed):
                    placed.append(box)
                    self.symbol(img, box, what, pen)
                    break

    def symbol(self, img, box, what, pen):
        (a, c), (d, e) = self.to_px(box[0], box[1]), self.to_px(box[2], box[3])
        p0, p1 = _pt((a, c)), _pt((d, e))
        cx, cy, w, h = (a + d) / 2, (c + e) / 2, d - a, e - c
        cv2.rectangle(img, p0, p1, pen, self.thin)
        if what == "bed":
            for k in (0.08, 0.55):
                cv2.rectangle(img, _pt((a + w * k, c + h * 0.05)), _pt((a + w * (k + 0.37), c + h * 0.17)), pen, self.thin)
            cv2.line(img, _pt((a, c + h * 0.3)), _pt((d, c + h * 0.3)), pen, self.thin)
        elif what == "wardrobe":
            cv2.line(img, p0, p1, pen, self.thin)
        elif what == "sofa":
            cv2.rectangle(img, _pt((a + w * 0.12, c + h * 0.25)), _pt((d - w * 0.12, e)), pen, self.thin)
        elif what in ("table", "desk"):
            n = 3 if what == "table" else 1
            for k in range(n):
                for y in (c - self.px(250), e + self.px(250)):
                    cv2.circle(img, _pt((a + w * (k + 0.5) / n, y)), int(self.px(220)), pen, self.thin)
            if what == "desk":
                _text(img, "CWSD", cx, cy, min(h * 0.4, 2 * PAPER), pen)
        elif what == "wc":
            cv2.ellipse(img, _pt((cx, c + h * 0.62)), (int(w * 0.45), int(h * 0.35)), 0, 0, 360, pen, self.thin)
        elif what == "basin":
            cv2.ellipse(img, _pt((cx, cy)), (int(w * 0.35), int(h * 0.3)), 0, 0, 360, pen, self.thin)
        elif what == "bath":
            cv2.rectangle(img, _pt((a + w * 0.06, c + h * 0.1)), _pt((d - w * 0.06, e - h * 0.1)), pen, self.thin)
        elif what == "stove":
            for fx in (0.3, 0.7):
                for fy in (0.3, 0.7):
                    cv2.circle(img, _pt((a + w * fx, c + h * fy)), int(w * 0.13), pen, self.thin)
        elif what == "chair":
            cv2.rectangle(img, _pt((a, c)), _pt((d, c + h * 0.25)), pen, self.thin)
        elif what == "counter":
            r = int(min(w, h) * 0.25)
            cv2.circle(img, _pt((a + w * 0.3, cy)), r, pen, self.thin)
            cv2.rectangle(img, _pt((a + w * 0.3 - 2 * r, cy - 1.3 * r)), _pt((a + w * 0.3 + 2 * r, cy + 1.3 * r)), pen, self.thin)

    def columns(self, img, b: Building, grid) -> list:
        """Columns at the building's corners and some grid crossings on its outer walls."""
        rng = self.rng
        if rng.random() > 0.35:
            return []
        size = float(rng.choice([230, 300, 400]))
        xs, ys = grid
        points = [(x, y) for x in xs for y in ys if (x in (0, b.w) or y in (0, b.h)) and rng.random() < 0.7]
        points += [p for p in ((0, 0), (b.w, 0), (0, b.h), (b.w, b.h)) if p not in points]
        found = []
        for x, y in points:
            if any(n[0] <= x <= n[2] and n[1] <= y <= n[3] for n in b.notches):
                continue  # on a cut-away corner
            (a, c), (d, e) = self.to_px(x - size / 2, y - size / 2), self.to_px(x + size / 2, y + size / 2)
            corners = _rect_pts(a, c, d, e)
            self.labels.append(("column", corners))
            found.append(corners)
            pts = np.array(corners, np.int32)
            if self.wall_style in ("hatch", "crosshatch") and rng.random() < 0.5:
                cv2.fillPoly(img, [pts], (255, 255, 255))
                _hatch(img, corners, 4, 45, self.ink)
                _hatch(img, corners, 4, -45, self.ink)
                cv2.polylines(img, [pts], True, self.ink, self.mid)
            else:
                cv2.fillPoly(img, [pts], self.ink)
        return found

    def room(self, img, rect, name, blockers):
        """The room's floor from wall face to wall face, as its rectangle less every wall, opening and column on it."""
        rng, ink = self.rng, self.ink
        (a, c), (d, e) = self.to_px(rect[0], rect[1]), self.to_px(rect[2], rect[3])
        x0, y0 = int(a) - 2, int(c) - 2
        mask = np.zeros((int(e) - y0 + 3, int(d) - x0 + 3), np.uint8)
        cv2.fillPoly(mask, [np.round(np.array(_rect_pts(a, c, d, e)) - [x0, y0]).astype(np.int32)], 1)
        for poly in blockers:
            p = np.round(np.asarray(poly) - [x0, y0]).astype(np.int32)
            if p[:, 0].max() >= 0 and p[:, 1].max() >= 0 and p[:, 0].min() < mask.shape[1] and p[:, 1].min() < mask.shape[0]:
                cv2.fillPoly(mask, [p], 0)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            contour = cv2.approxPolyDP(max(contours, key=cv2.contourArea), 0.7, True)[:, 0]
            if len(contour) >= 3:
                self.labels.insert(0, ("space", (contour + [x0, y0]).astype(float).tolist()))
        cx, cy = (a + d) / 2, (c + e) / 2 + rng.uniform(-0.2, 0.2) * (e - c)
        size = rng.uniform(2.2, 3.5) * PAPER
        _text(img, name, cx, cy, size, ink)
        if rng.random() < 0.7:
            area = (rect[2] - rect[0]) * (rect[3] - rect[1]) / 1e6
            _text(img, str(rng.choice([f"{area:.1f} m2", f"{area:.0f} M2", f"A = {area:.2f}"])), cx, cy + size * 1.6, size * 0.7, ink)
        if rng.random() < 0.3:
            level = f"{rng.choice(['+', '-'])}{rng.choice([0.0, 0.15, 0.3, 2.8, 5.6]):.2f}"
            lx, ly = cx + (d - a) * 0.25, cy + (e - c) * 0.25
            tri = np.array([_pt((lx, ly)), _pt((lx - 12, ly - 16)), _pt((lx + 12, ly - 16))], np.int32)
            cv2.polylines(img, [tri], True, ink, self.thin)
            _text(img, level, lx + 20, ly - 8, 2 * PAPER, ink, centre=False)

    def tags(self, img, b: Building):
        rng, ink = self.rng, self.ink
        counts = {"door": 0, "window": 0}
        r = int(3.5 * PAPER)
        for seg in b.segments:
            for o0, o1, kind, _ in seg.openings:
                if kind == "opening" or rng.random() > 0.6:
                    continue
                counts[kind] += 1
                mid, off = (o0 + o1) / 2, seg.thickness / 2 + rng.uniform(400, 900)
                side = rng.choice([-1, 1])
                c = _pt(self.to_px(mid, seg.pos + side * off) if seg.horizontal else self.to_px(seg.pos + side * off, mid))
                name = f"{'D' if kind == 'door' else 'W'}{counts[kind] if rng.random() < 0.7 else rng.integers(1, 12)}"
                if kind == "door":
                    cv2.circle(img, c, r, ink, self.thin)
                else:
                    hexagon = [_pt((c[0] + r * 1.2 * np.cos(t), c[1] + r * np.sin(t))) for t in np.linspace(0, 2 * np.pi, 7)[:-1]]
                    cv2.polylines(img, [np.array(hexagon, np.int32)], True, ink, self.thin)
                _text(img, name, c[0], c[1], r * 0.8, ink)

    def dimensions(self, img, b: Building, grid):
        """Dimension chains in up to three tiers on some sides, with extension lines, and sometimes one inside."""
        rng, ink = self.rng, self.ink
        xs, ys = grid
        text_h = rng.uniform(1.8, 2.5) * PAPER
        tick = int(1.2 * PAPER)
        openings_x = sorted({v for s in b.segments if s.horizontal and s.pos == 0 for o in s.openings for v in o[:2]} | {0, b.w})
        openings_y = sorted({v for s in b.segments if not s.horizontal and s.pos == 0 for o in s.openings for v in o[:2]} | {0, b.h})
        walls_x = sorted({s.pos for s in b.segments if not s.horizontal})
        walls_y = sorted({s.pos for s in b.segments if s.horizontal})
        sides = [k for k in range(4) if rng.random() < 0.75] or [0]
        for side in sides:
            horizontal = side in (0, 1)
            chains = [openings_x if horizontal else openings_y, walls_x if horizontal else walls_y, [0, b.w] if horizontal else [0, b.h]]
            tiers = rng.integers(1, 4)
            for k, chain in enumerate(chains[:tiers]):
                off = 900 + 700 * k
                for a, c in zip(chain, chain[1:]):
                    if c - a < 100:
                        continue
                    if horizontal:
                        y = -off if side == 0 else b.h + off
                        p, q = self.to_px(a, y), self.to_px(c, y)
                        cv2.line(img, _pt(p), _pt(q), ink, self.thin)
                        _text(img, f"{int(c - a)}", (p[0] + q[0]) / 2, p[1] - text_h * 0.9, text_h, ink)
                    else:
                        x = -off if side == 2 else b.w + off
                        p, q = self.to_px(x, a), self.to_px(x, c)
                        cv2.line(img, _pt(p), _pt(q), ink, self.thin)
                        _text(img, f"{int(c - a)}", p[0] - text_h * 0.9, (p[1] + q[1]) / 2, text_h, ink, vertical=True)
                    for e in (p, q):
                        cv2.line(img, _pt((e[0] - tick, e[1] + tick)), _pt((e[0] + tick, e[1] - tick)), ink, self.thin)
            near, far = 200, 900 + 700 * (tiers - 1)
            for v in chains[0]:  # extension lines from the building out to the last tier
                if horizontal:
                    ends = [self.to_px(v, -d if side == 0 else b.h + d) for d in (near, far)]
                else:
                    ends = [self.to_px(-d if side == 2 else b.w + d, v) for d in (near, far)]
                cv2.line(img, _pt(ends[0]), _pt(ends[1]), (120, 120, 120), 1)
        if rng.random() < 0.4 and len(walls_x) > 2:  # a chain across the rooms
            y = rng.uniform(0.2, 0.8) * b.h
            for a, c in zip(walls_x, walls_x[1:]):
                p, q = self.to_px(a, y), self.to_px(c, y)
                cv2.line(img, _pt(p), _pt(q), ink, self.thin)
                for e in (p, q):
                    cv2.line(img, _pt((e[0] - tick, e[1] + tick)), _pt((e[0] + tick, e[1] - tick)), ink, self.thin)
                _text(img, f"{int(c - a)}", (p[0] + q[0]) / 2, p[1] - text_h * 0.9, text_h, ink)

    def section_mark(self, img, b: Building):
        rng, ink = self.rng, self.ink
        letter = str(rng.choice(list("ABCD")))
        r = int(5 * PAPER)
        if rng.random() < 0.5:
            y = rng.uniform(0.2, 0.8) * b.h
            ends = [self.to_px(-1800, y), self.to_px(b.w + 1800, y)]
        else:
            x = rng.uniform(0.2, 0.8) * b.w
            ends = [self.to_px(x, -1800), self.to_px(x, b.h + 1800)]
        _dashed(img, ends[0], ends[1], ink, self.mid, [self.px(900), self.px(150), self.px(100), self.px(150)])
        for e in ends:
            cv2.circle(img, _pt(e), r, ink, self.thin)
            cv2.line(img, _pt((e[0] - r, e[1])), _pt((e[0] + r, e[1])), ink, self.thin)
            _text(img, letter, e[0], e[1] - r * 0.45, r * 0.7, ink)

    def overhead(self, img, b: Building):
        """Roof overhang and beams above the cut, drawn dashed: not walls."""
        rng = self.rng
        pattern = [self.px(300), self.px(200)]
        m = rng.uniform(450, 900)
        (a, c), (d, e) = self.to_px(-m, -m), self.to_px(b.w + m, b.h + m)
        corners = [(a, c), (d, c), (d, e), (a, e)]
        for p, q in zip(corners, corners[1:] + corners[:1]):
            _dashed(img, p, q, self.ink, self.thin, pattern)
        for _ in range(rng.integers(0, 3)):
            x = rng.uniform(0.15, 0.85) * b.w
            for off in (-120, 120):
                _dashed(img, self.to_px(x + off, 0), self.to_px(x + off, b.h), self.ink, self.thin, pattern)

    # --- around the plans ----------------------------------------------------------

    def sheet(self, img, x0, width):
        rng, ink = self.rng, self.ink
        h, w = img.shape[:2]
        cv2.rectangle(img, (15, 15), (w - 15, h - 15), ink, self.thick)
        cv2.line(img, (x0, 15), (x0, h - 15), ink, self.mid)
        row, y, pad = 2.6 * PAPER, 15 + 3 * PAPER, int(3 * PAPER)
        # notes
        _text(img, "NOTES", x0 + pad, y + row, 3 * PAPER, ink, centre=False)
        y += 2 * row
        for k in range(rng.integers(3, 9)):
            words = " ".join(rng.choice(WORDS, rng.integers(4, 9)))
            _text(img, f"{k + 1}. {words}"[:max(10, int(width / (1.6 * PAPER)))], x0 + pad, y, 1.8 * PAPER, ink, centre=False)
            y += row
        # legend: wall swatches that are not walls
        if rng.random() < 0.6:
            y += row
            _text(img, "LEGEND", x0 + pad, y, 3 * PAPER, ink, centre=False)
            for label in rng.choice(["225MM BLOCKWALL", "150MM BLOCKWALL", "PARTITION", "CONCRETE", "TILES"], 3, replace=False):
                y += row * 1.6
                sw = _rect_pts(x0 + pad, y - row, x0 + pad + 10 * PAPER, y)
                self.wall(img, sw, False)
                _text(img, str(label), x0 + pad + 13 * PAPER, y - 2, 1.8 * PAPER, ink, centre=False)
        # door and window schedule
        if rng.random() < 0.5 and y < h * 0.45:
            y += row * 2
            cols = [x0 + pad, x0 + pad + width * 0.15, x0 + pad + width * 0.45, x0 + width - pad]
            for k in range(rng.integers(4, 9)):
                cv2.line(img, _pt((cols[0], y)), _pt((cols[-1], y)), ink, self.thin)
                for c, s in zip(cols, (f"{rng.choice(['D', 'W'])}{k + 1}", f"{rng.integers(6, 25) * 100}x2100", str(rng.choice(['FLUSH', 'PANEL', 'CASEMENT', 'SLIDING'])))):
                    _text(img, s, c + 6, y + row * 0.75, 1.7 * PAPER, ink, centre=False)
                y += row
            cv2.line(img, _pt((cols[0], y)), _pt((cols[-1], y)), ink, self.thin)
            for c in cols:
                cv2.line(img, _pt((c, y - row * (k + 1))), _pt((c, y)), ink, self.thin)
        # north arrow
        c, r = (int(x0 + width / 2), int(h * 0.62)), int(min(width * 0.12, 12 * PAPER))
        cv2.circle(img, c, r, ink, self.thin)
        cv2.fillPoly(img, [np.array([(c[0], c[1] - r), (c[0] - r // 3, c[1] + r // 2), (c[0], c[1] + r // 4)], np.int32)], ink)
        _text(img, "N", c[0], c[1] - r - 4 * PAPER, 4 * PAPER, ink)
        # title block
        top = int(h * 0.72)
        cv2.line(img, (x0, top), (w - 15, top), ink, self.mid)
        fields = ["CLIENT", "PROJECT", "DRAWING TITLE", "DRAWN", "CHECKED", "DATE", f"SCALE 1:{self.denominator}", "DRG No."]
        step = (h - 15 - top) / len(fields)
        for k, f in enumerate(fields):
            yy = top + step * (k + 1)
            if k < len(fields) - 1:
                cv2.line(img, _pt((x0, yy)), _pt((w - 15, yy)), ink, self.thin)
            _text(img, f, x0 + pad, yy - step * 0.35, min(2.2 * PAPER, step * 0.5), ink, centre=False)
        if rng.random() < 0.5:
            cv2.circle(img, _pt((w - 15 - width * 0.2, top + step * 1.5)), int(min(step, width * 0.12)), ink, self.mid)

    def degrade(self, img):
        rng = self.rng
        if rng.random() < 0.15:  # aged or tinted paper
            img = np.clip(img.astype(np.float32) * np.array(rng.choice([(1, 0.97, 0.9), (0.95, 0.97, 1), (0.97, 0.97, 0.97)])), 0, 255).astype(np.uint8)
        if rng.random() < 0.4:
            factor = rng.uniform(0.5, 0.85)
            small = cv2.resize(img, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
            img = cv2.resize(small, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
        if rng.random() < 0.3:
            img = cv2.GaussianBlur(img, (3, 3), 0)
        if rng.random() < 0.3:
            noise = rng.normal(0, rng.uniform(3, 12), img.shape)
            img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        if rng.random() < 0.2:
            img = np.clip(img.astype(np.float32) * rng.uniform(0.85, 1.0) + rng.uniform(0, 20), 0, 255).astype(np.uint8)
        return img


def make_plan(seed: int) -> tuple[np.ndarray, dict]:
    sheet = Sheet(np.random.default_rng(seed))
    image = sheet.draw()
    h, w = image.shape[:2]
    detections = []
    for n, (name, polygon) in enumerate(sheet.labels):
        polygon = [[round(float(x), 1), round(float(y), 1)] for x, y in polygon]
        xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
        detections.append({"id": f"det_0_{n:03d}", "class_name": name, "score": 1.0, "bbox": [min(xs), min(ys), max(xs), max(ys)],
                           "polygon": polygon, "coordinate_space": "page_pixels"})
    page = {"page_index": 0, "image_size": {"width": w, "height": h},
            "scale": {"meters_per_pixel": sheet.denominator * 0.0254 / DPI, "denominator": float(sheet.denominator), "source": "drawing"},
            "detections": detections}
    return image, {"id": f"synth-{seed}", "status": "succeeded", "pages": [page]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    root = Path(load_config(args.config)["data"]["root"])
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "labels").mkdir(parents=True, exist_ok=True)
    for seed in range(args.seed, args.seed + args.count):
        image, labels = make_plan(seed)
        name = f"synth_{seed:05d}"
        cv2.imwrite(str(root / "sources" / f"{name}.png"), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        (root / "labels" / f"{name}.json").write_text(json.dumps(labels))
    print(f"{args.count} synthetic plans in {root}/sources and {root}/labels")


if __name__ == "__main__":
    main()
