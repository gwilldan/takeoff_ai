"""
Agent validation of candidate walls, by showing rather than telling.

The geometry stage produces candidate wall centrelines from parallel face pairs.
Some are not walls: a setting-out grid line paired with a dimension line, a
hatch boundary, a furniture outline. Deciding which is which is a judgement about
what the drawing means, which is exactly what a vision model is good at — and
exactly what the geometry cannot settle on its own.

So the candidates are drawn back onto the page, numbered, and the model is asked
which numbers are real walls. It never returns a coordinate: the numbers it sees
map to geometry that was measured exactly. Getting a label wrong costs one
mislabelled component; getting a coordinate wrong would put every annotation in
the wrong place.
"""

from __future__ import annotations

import base64
import json

import fitz

from config import LLM_VISION_MODEL
from extractor.graph import WallRun
from extractor.llm import TokenTracker, chat_with_tracking, parse_json_response
from extractor.pdf_utils import load_page, open_pdf_document

OVERLAY_COLOR = (0.0, 0.45, 1.0)
OVERLAY_WIDTH = 1.6
LABEL_FONTSIZE = 7

# Past this many candidates the overlay is unreadable and the reply unreliable.
MAX_LABELLED_CANDIDATES = 60

VALIDATE_SYSTEM = """You are checking automated wall detection on an architectural plan.

Each candidate has been drawn over the plan as a blue line with a number beside
it. Every candidate was derived from a pair of parallel lines in the drawing, so
each one is either a real wall or something that merely looks like one.

Classify EVERY number you can see. Real walls are the building's fabric —
external walls, internal partitions. These are NOT walls:
- setting-out grid lines (usually red, dashed, running past the building)
- dimension lines and their extension lines, outside the building
- hatching or fill boundaries inside a wall zone
- furniture, fittings, sanitary ware, vehicle or landscape outlines
- section markers, leader lines, north points, title block rules

Return a single JSON object, no markdown:
{
  "walls": [1, 2, 5],
  "grid": [3],
  "dimensions": [4],
  "other": [6],
  "notes": ["anything ambiguous"]
}

Every number you were shown must appear in exactly one list. If you genuinely
cannot tell what a candidate is, put it in "walls" — a surveyor deleting one
wrong wall is a smaller problem than a missing wall nobody notices.
"""


def render_candidates_overlay(
    pdf_path: str,
    page_index: int,
    walls: list[WallRun],
    dpi: int = 150,
) -> str:
    """
    Render the page with numbered candidate centrelines drawn over it.

    The drawing is done on the in-memory page and never saved, so the source PDF
    on the shared volume is untouched.
    """
    doc = open_pdf_document(pdf_path)
    try:
        page = load_page(doc, page_index)

        for index, wall in enumerate(walls[:MAX_LABELLED_CANDIDATES], start=1):
            start = fitz.Point(*wall.start)
            end = fitz.Point(*wall.end)
            page.draw_line(start, end, color=OVERLAY_COLOR, width=OVERLAY_WIDTH)

            midpoint = fitz.Point(
                (wall.start[0] + wall.end[0]) / 2,
                (wall.start[1] + wall.end[1]) / 2,
            )
            page.insert_text(
                midpoint,
                str(index),
                fontsize=LABEL_FONTSIZE,
                color=OVERLAY_COLOR,
            )

        zoom = dpi / 72
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return base64.b64encode(pixmap.tobytes("png")).decode("ascii")
    finally:
        doc.close()


def parse_verdicts(payload: dict, candidate_count: int) -> dict[int, str]:
    """
    Turn the model's lists into a per-candidate verdict.

    Anything the model failed to mention is kept as a wall. A candidate that
    silently disappears is a wall missing from the take-off, which is worse than
    one the reviewer has to delete.
    """
    verdicts: dict[int, str] = {}

    for label in ("walls", "grid", "dimensions", "other"):
        for number in payload.get(label) or []:
            try:
                index = int(number)
            except (TypeError, ValueError):
                continue
            if 1 <= index <= candidate_count:
                verdicts.setdefault(index, label)

    for index in range(1, candidate_count + 1):
        verdicts.setdefault(index, "walls")

    return verdicts


def validate_walls(
    pdf_path: str,
    page_index: int,
    page_number: int,
    walls: list[WallRun],
    tracker: TokenTracker,
) -> tuple[list[WallRun], list[str]]:
    """
    Keep the candidates the agent calls walls.

    Returns the surviving walls and any notes. On any failure — no candidates,
    an unreachable model, unparseable JSON — every candidate is kept and a note
    explains why, so a broken model degrades the output's precision rather than
    emptying it.
    """
    if not walls:
        return [], []

    labelled = walls[:MAX_LABELLED_CANDIDATES]
    notes: list[str] = []

    if len(walls) > MAX_LABELLED_CANDIDATES:
        notes.append(
            f"{len(walls) - MAX_LABELLED_CANDIDATES} wall candidates beyond the first "
            f"{MAX_LABELLED_CANDIDATES} were kept without agent review"
        )

    try:
        response = chat_with_tracking(
            tracker,
            step=f"validate_walls_p{page_number}",
            model=LLM_VISION_MODEL,
            messages=[
                {"role": "system", "content": VALIDATE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Page {page_number}. {len(labelled)} candidates are numbered "
                        f"1 to {len(labelled)} in blue. Classify every number."
                    ),
                    "images": [
                        render_candidates_overlay(pdf_path, page_index, labelled)
                    ],
                },
            ],
        )
        payload = parse_json_response(response.content)
    except Exception as error:
        return walls, [f"Wall validation skipped, all candidates kept: {error}"]

    verdicts = parse_verdicts(payload, len(labelled))
    kept = [wall for index, wall in enumerate(labelled, start=1) if verdicts[index] == "walls"]
    kept.extend(walls[MAX_LABELLED_CANDIDATES:])

    rejected = len(labelled) - sum(1 for v in verdicts.values() if v == "walls")
    if rejected:
        notes.append(f"Agent rejected {rejected} of {len(labelled)} wall candidates")

    for note in payload.get("notes") or []:
        if isinstance(note, str):
            notes.append(note)

    return kept, notes
