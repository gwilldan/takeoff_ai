"""
Name and verify the extracted components with the vision model.

The geometry is already fixed by `walls.py`, `rooms.py` and `openings.py`.
What pixels cannot tell is meaning: is this pocket a bedroom, a stair flight,
a shaft or a title-block cell; is this gap a door or a window. A vision model
reads that well, but places points poorly, so it is never asked for a
coordinate. Instead every candidate is drawn on the page with a number
("set-of-marks" prompting) and the model answers per number.

What the answers may change:

- An unnamed room takes the model's name. A named room keeps the drawing's
  own text.
- A room the model says is not a room is dropped, unless the drawing tags it
  with a printed area (the drawing outranks the model).
- An untagged opening takes the model's door/window call, or is dropped when
  the model says it is not an opening. A tagged opening keeps its tag.

Without a reachable model everything passes through unchanged.
"""

from __future__ import annotations

import base64
import logging
import math

import cv2
import numpy as np

from app.config import settings
from app.extractor.llm import TokenTracker, chat_with_tracking, parse_json_response
from app.extractor.openings import Opening, number_openings
from app.extractor.render import render_page_array
from app.extractor.rooms import UNNAMED, Room

log = logging.getLogger(__name__)

IMAGE_LONG_SIDE_PX = 2400
MARGIN_PT = 20.0
MAX_MARKS = 90

LABEL_SYSTEM = """You verify components extracted from one architectural floor plan.

The image is the plan with numbered marks drawn over it:
- R<n> (blue outline and label) marks a candidate room.
- O<n> (magenta line and label) marks a candidate opening in a wall.

For every mark, judge from the drawing itself:
- Rooms: the room's name, from the text written inside it when legible,
  otherwise the conventional name for what is drawn (BEDROOM, TOILET,
  KITCHEN, CORRIDOR, STORE, STAIRCASE, VERANDA ...). is_room is false when
  the outline is not a usable room: outside the building, inside furniture,
  a stair flight, a shaft or void, a title-block cell, a table or legend.
- Openings: "door" (swing arc, door leaf or doorway), "window" (glazing
  lines in an external wall), or "not_an_opening" (no opening is drawn there).

Return a single JSON object, no markdown, with an entry for every mark:
{"rooms": {"R1": {"name": "BEDROOM", "is_room": true}},
 "openings": {"O1": "door"}}
"""


def _label(image: np.ndarray, text: str, at: tuple[int, int], colour: tuple[int, int, int]) -> None:
    scale, thickness = 0.55, 1
    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    x, y = at[0] - w // 2, at[1] + h // 2
    cv2.rectangle(image, (x - 2, y - h - 3), (x + w + 2, y + 3), (255, 255, 255), -1)
    cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thickness, cv2.LINE_AA)


def _marked_page(cache, rooms: list[Room], openings: list[Opening]) -> str:
    """The plan cropped to the marks, with every candidate numbered, as base64 PNG."""
    points = np.array([p for c in (*rooms, *openings) for p in c.polyline], dtype=float)
    x0, y0 = np.maximum(points.min(axis=0) - MARGIN_PT, 0)
    x1, y1 = np.minimum(points.max(axis=0) + MARGIN_PT, [cache.page_width_pts, cache.page_height_pts])
    zoom = IMAGE_LONG_SIDE_PX / max(x1 - x0, y1 - y0)
    page = render_page_array(cache.pdf_path, cache.page_index, zoom)
    image = np.ascontiguousarray(page[int(y0 * zoom):int(y1 * zoom), int(x0 * zoom):int(x1 * zoom), ::-1])

    def px(point) -> tuple[int, int]:
        return int((point[0] - x0) * zoom), int((point[1] - y0) * zoom)

    blue, magenta = (200, 80, 0), (200, 0, 200)
    for index, room in enumerate(rooms, start=1):
        cv2.polylines(image, [np.array([px(p) for p in room.polyline], np.int32)], True, blue, 2)
        _label(image, f"R{index}", px(np.mean(room.polyline, axis=0)), blue)
    for index, opening in enumerate(openings, start=1):
        a, b = px(opening.polyline[0]), px(opening.polyline[1])
        cv2.line(image, a, b, magenta, 3)
        _label(image, f"O{index}", ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2 - 12), magenta)

    ok, png = cv2.imencode(".png", image)
    return base64.b64encode(png.tobytes()).decode("ascii")


def _groups(rooms: list[Room], openings: list[Opening]) -> list[tuple[list[Room], list[Opening]]]:
    """Split the marks into spatial tiles of at most MAX_MARKS, so each image stays legible."""
    items = [*rooms, *openings]
    if len(items) <= MAX_MARKS:
        return [(rooms, openings)]
    centres = np.array([np.mean(c.polyline, axis=0) for c in items])
    side = math.ceil(math.sqrt(len(items) / MAX_MARKS))
    low, span = centres.min(axis=0), np.ptp(centres, axis=0) + 1e-6
    cells = np.minimum((((centres - low) / span) * side).astype(int), side - 1)
    groups = []
    for cell in sorted({tuple(c) for c in cells}):
        members = [item for item, c in zip(items, cells) if tuple(c) == cell]
        groups.append(([m for m in members if isinstance(m, Room)], [m for m in members if isinstance(m, Opening)]))
    return groups


def _ask(cache, tracker: TokenTracker, rooms: list[Room], openings: list[Opening], part: str) -> dict | None:
    try:
        response = chat_with_tracking(
            tracker, step=f"label_components_{cache.page_number}{part}", model=settings.llm_vision_model,
            messages=[
                {"role": "system", "content": LABEL_SYSTEM},
                {"role": "user", "content": f"{len(rooms)} room marks (R1–R{len(rooms)}), "
                                            f"{len(openings)} opening marks (O1–O{len(openings)}).",
                 "images": [_marked_page(cache, rooms, openings)]},
            ],
        )
        return parse_json_response(response.content)
    except Exception as error:  # network, auth, or malformed JSON
        log.warning("page %d: labelling fell back to the drawing's own text: %s", cache.page_number, error)
        return None


def label_components(cache, tracker: TokenTracker, rooms: list[Room], openings: list[Opening]) -> tuple[list[Room], list[Opening]]:
    rejected: set[int] = set()
    groups = _groups(rooms, openings)
    for number, (group_rooms, group_openings) in enumerate(groups, start=1):
        if not group_rooms and not group_openings:
            continue
        part = f"_part{number}" if len(groups) > 1 else ""
        answer = _ask(cache, tracker, group_rooms, group_openings, part)
        if answer is None:
            continue

        room_answers = answer.get("rooms") or {}
        for index, room in enumerate(group_rooms, start=1):
            verdict = room_answers.get(f"R{index}") or {}
            name = str(verdict.get("name") or "").strip().upper()
            if room.name == UNNAMED and name:
                room.name = name
                room.notes.append("Named by the vision model")
            if verdict.get("is_room") is False:
                if room.area_m2 is None:
                    rejected.add(id(room))
                else:
                    room.notes.append("The vision model doubts this is a room; kept because the drawing labels it")

        opening_answers = answer.get("openings") or {}
        for index, opening in enumerate(group_openings, start=1):
            verdict = str(opening_answers.get(f"O{index}") or "").lower()
            if opening.reference is not None:
                continue
            if verdict == "not_an_opening":
                rejected.add(id(opening))
            elif verdict in ("door", "window") and verdict != opening.kind:
                opening.notes.append(f"Type set to {verdict} by the vision model")
                opening.kind = verdict

    gone = {room.id for room in rooms if id(room) in rejected}
    rooms = [room for room in rooms if id(room) not in rejected]
    openings = [opening for opening in openings if id(opening) not in rejected]
    for opening in openings:
        opening.room_ids = [i for i in opening.room_ids if i not in gone]
    return rooms, number_openings(openings)
