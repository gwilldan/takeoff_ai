"""
Page classification — is this page a construction plan, and how is it drawn?

Two stages, in this order for a reason. A drawing set is mostly not plans: cover
sheets, door schedules, specification text, revision tables. Sending every page
to a vision model costs a call per page for an answer that page geometry already
gives away — a page with no vectors cannot be a plan. So a free deterministic
pre-filter runs first, and only survivors reach the model.

The vision call then does classification and profiling together. Those used to
be two separate concerns but they need the same image and the same summary, so
merging them halves the calls per page.
"""

from __future__ import annotations

import json
import re

from config import LLM_VISION_MODEL
from extractor.cache import ExtractionCache
from extractor.llm import TokenTracker, chat_with_tracking, parse_json_response
from extractor.render import render_page_base64
from tools.query import detect_scale_from_text, get_color_summary

# A plan is drawn. Fewer strokes than this and there is nothing to measure.
MIN_LINE_SEGMENTS = 40

# Vocabulary that appears on plans but rarely on schedules or specifications.
PLAN_KEYWORDS = re.compile(
    r"\b(PLAN|FLOOR|LAYOUT|SECTION|ELEVATION|SITE|BEDROOM|KITCHEN|TOILET|"
    r"BATHROOM|LOBBY|SITTING|LOUNGE|VERANDA|PORCH|STORE|SCALE)\b",
    re.IGNORECASE,
)

# Titles that positively identify a page as something other than a plan.
NON_PLAN_TITLES = re.compile(
    r"\b(SCHEDULE|SPECIFICATION|BILL OF QUANTITIES|REVISION HISTORY|"
    r"DRAWING (LIST|REGISTER)|CONTENTS|NOTES? SHEET|LEGEND SHEET)\b",
    re.IGNORECASE,
)


def fallback_profile(
    cache: ExtractionCache,
    scale_str: str | None,
    scale_ratio: int | None,
) -> dict:
    """
    Drawing conventions guessed from geometry alone.

    Used when the model is unreachable or returns unparseable JSON. The most
    frequent line colour is the best available guess at the wall colour, and
    confidence says plainly that this was not read from the drawing.
    """
    colors = get_color_summary(cache)
    frequent = [c["color_rgb"] for c in colors["colors"][:3] if c.get("color_rgb")]

    return {
        "scale": scale_str or "unknown",
        "scale_ratio": scale_ratio or 100,
        "wall_indicators": {
            "description": "fallback — most frequent line colour",
            "likely_colors": frequent[:1] or [[0, 0, 0]],
            "likely_stroke_range": [0.0, 5.0],
        },
        "grid_indicators": {
            "description": "unknown",
            "likely_colors": [],
            "exclude_from_walls": True,
        },
        "dimension_style": {
            "description": "3-5 digit millimetre values",
            "location": "unknown",
            "text_pattern": r"\d{3,5}",
        },
        "room_labels": {"description": "uppercase room names", "examples": []},
        "opening_style": {
            "description": "Dr-/Wn- references",
            "door_pattern": r"Dr[-\s]?\d+",
            "window_pattern": r"Wn[-\s]?\d+",
        },
        "confidence": "low",
        "notes": [],
    }


def prefilter_page(cache: ExtractionCache) -> tuple[bool, str]:
    """
    Cheap, model-free judgement on whether a page is worth a vision call.

    Returns (is_candidate, reason). The reason is carried into the result so a
    skipped page can explain itself rather than silently vanishing.
    """
    if len(cache.line_segments) < MIN_LINE_SEGMENTS:
        return False, (
            f"only {len(cache.line_segments)} line segments — "
            "too little geometry to be a drawing"
        )

    heading = cache.title_block_text[:400]
    if NON_PLAN_TITLES.search(heading) and not PLAN_KEYWORDS.search(heading):
        return False, "title reads as a schedule or specification, not a drawing"

    return True, "has drawing geometry"


CLASSIFY_SYSTEM = """You are an architectural drawing analyst.

You receive one page from a construction drawing set: an image plus a compact
summary of its vector and text content. Decide whether this page is a
construction plan that can be measured, and if so describe how THIS drawing is
drawn — its own conventions, not generic ones.

A construction plan is a scaled orthographic drawing of a building or site:
floor plans, site plans, setting-out plans, roof plans. These are NOT plans:
door and window schedules, specifications, bills of quantities, revision tables,
drawing registers, title or cover sheets, 3D views, photographs.

Return a single JSON object, no markdown:
{
  "is_plan": true,
  "plan_type": "ground_floor_plan | upper_floor_plan | site_plan | roof_plan | section | elevation | detail | schedule | cover | other",
  "not_plan_reason": "only when is_plan is false",
  "scale": "1:100 or unknown",
  "scale_ratio": 100,
  "wall_indicators": {
    "description": "how walls appear in this drawing",
    "likely_colors": [[r, g, b]],
    "likely_stroke_range": [min, max]
  },
  "grid_indicators": {
    "description": "how setting-out grid lines appear, if any",
    "likely_colors": [[r, g, b]],
    "exclude_from_walls": true
  },
  "dimension_style": {
    "description": "where and how dimensions are shown",
    "location": "exterior | interior | both | unknown",
    "text_pattern": "regex for dimension numbers, e.g. \\\\d{3,5}"
  },
  "room_labels": { "description": "how room names appear", "examples": ["BEDROOM"] },
  "opening_style": {
    "description": "how doors and windows are referenced",
    "door_pattern": "Dr-\\\\d+",
    "window_pattern": "Wn-\\\\d+"
  },
  "confidence": "high | medium | low",
  "notes": ["assumptions or ambiguities"]
}

RGB values are 0.0-1.0. Use the colour summary to infer likely_colors, and keep
grid colours out of wall colours — they are usually red and dashed.
Sections and elevations are drawings but cannot be measured in plan: mark them
is_plan false with plan_type set accordingly.
"""


def classify_page(cache: ExtractionCache, tracker: TokenTracker) -> dict:
    """
    Classify and profile one page with a single vision call.

    Falls back to heuristics when the model is unreachable or returns
    unparseable JSON — a page is never dropped because of a bad response, it is
    kept with low confidence and a note.
    """
    is_candidate, reason = prefilter_page(cache)

    if not is_candidate:
        return {
            "is_plan": False,
            "plan_type": "other",
            "not_plan_reason": reason,
            "classified_by": "prefilter",
            "confidence": "high",
            "notes": [],
        }

    # Search the whole page, not just the leading extract. A view title carrying
    # "1 : 100" is often the last thing in the text stream rather than the first,
    # so reading only title_block_text reported "unknown" on drawings that state
    # their scale plainly.
    scale_str, scale_ratio = detect_scale_from_text(cache.full_text)
    summary = cache.to_summary()
    colors = get_color_summary(cache)

    user_text = (
        "Classify this drawing page and, if it is a measurable plan, return its "
        "drawing profile.\n\n"
        f"Page {cache.page_number}\n"
        f"Title block sample:\n{summary['title_block_text'][:600]}\n\n"
        f"Colour summary: {json.dumps(colors['colors'][:8])}\n"
        f"Text spans: {summary['text_span_count']}\n"
        f"Line segments: {summary['line_segment_count']}\n"
        f"Rectangles: {summary['rect_count']}\n"
        f"Sample labels: {summary['sample_text_labels'][:20]}\n"
    )
    if scale_str:
        user_text += f"\nScale detected in the title block: {scale_str}\n"

    try:
        response = chat_with_tracking(
            tracker,
            step=f"classify_page_{cache.page_number}",
            model=LLM_VISION_MODEL,
            messages=[
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {
                    "role": "user",
                    "content": user_text,
                    "images": [render_page_base64(cache.pdf_path, cache.page_index)],
                },
            ],
        )
        profile = parse_json_response(response.content)
        profile["classified_by"] = "vision"
    except Exception as error:  # network, auth, or malformed JSON
        profile = fallback_profile(cache, scale_str, scale_ratio)
        profile["is_plan"] = True
        profile["plan_type"] = "unknown"
        profile["classified_by"] = "fallback"
        profile.setdefault("notes", []).append(
            f"Classification fell back to heuristics: {error}"
        )

    if scale_str and profile.get("scale") in (None, "", "unknown"):
        profile["scale"] = scale_str
    if scale_ratio and not profile.get("scale_ratio"):
        profile["scale_ratio"] = scale_ratio

    profile.setdefault("is_plan", False)
    profile.setdefault("plan_type", "other")
    profile.setdefault("scale", "unknown")
    profile.setdefault("scale_ratio", 100)
    profile.setdefault("confidence", "low")
    profile.setdefault("notes", [])

    return profile
