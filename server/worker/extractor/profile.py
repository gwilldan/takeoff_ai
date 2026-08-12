"""
Per-document drawing profile — one LLM call with image + compact summary.

Learns THIS plan's visual conventions (wall color, dimension style, etc.)
instead of assuming firm-wide standards.
"""

from __future__ import annotations

import json
from typing import Optional

from extractor.cache import ExtractionCache
from extractor.llm import TokenTracker, chat_with_tracking, parse_json_response
from extractor.render import render_page_base64
from tools.query import detect_scale_from_text, get_color_summary
from config import LLM_VISION_MODEL


PROFILE_SYSTEM = """You are an architectural drawing analyst.

You receive a floor plan image plus a compact summary of its vector/text content.
Your job is to describe THIS drawing's visual conventions — not generic rules.

Return a single JSON object (no markdown) with this schema:
{
  "drawing_type": "ground_floor_plan | first_floor_plan | elevation | unknown",
  "scale": "1:100 or unknown",
  "scale_ratio": 100,
  "wall_indicators": {
    "description": "how walls appear in this drawing",
    "likely_colors": [[r, g, b]],
    "likely_stroke_range": [min, max]
  },
  "grid_indicators": {
    "description": "how grid lines appear, if any",
    "likely_colors": [[r, g, b]],
    "exclude_from_walls": true
  },
  "dimension_style": {
    "description": "where and how dimensions are shown",
    "location": "exterior | interior | both | unknown",
    "text_pattern": "regex pattern for dimension numbers e.g. \\\\d{3,5}"
  },
  "room_labels": {
    "description": "how room names appear",
    "examples": ["BEDROOM", "KITCHEN"]
  },
  "opening_style": {
    "description": "how doors/windows are referenced",
    "door_pattern": "Dr-\\\\d+",
    "window_pattern": "Wn-\\\\d+"
  },
  "confidence": "high | medium | low",
  "notes": ["assumptions or ambiguities"]
}

Use the color summary to infer likely_colors. RGB values are 0.0-1.0.
If scale appears in the title block text, include it.
"""


def infer_drawing_profile(
    cache: ExtractionCache,
    tracker: TokenTracker,
) -> dict:
    scale_str, scale_ratio = detect_scale_from_text(cache.title_block_text)
    summary = cache.to_summary()
    color_info = get_color_summary(cache)

    user_text = (
        "Analyze this architectural floor plan and return its drawing profile JSON.\n\n"
        f"Title block sample:\n{summary['title_block_text'][:600]}\n\n"
        f"Color summary: {json.dumps(color_info['colors'][:8])}\n"
        f"Text span count: {summary['text_span_count']}\n"
        f"Line segment count: {summary['line_segment_count']}\n"
        f"Sample labels: {summary['sample_text_labels'][:20]}\n"
    )
    if scale_str:
        user_text += f"\nDetected scale from title block: {scale_str}\n"

    page_image = render_page_base64(cache.pdf_path, cache.page_index)

    response = chat_with_tracking(
        tracker,
        step="drawing_profile",
        model=LLM_VISION_MODEL,
        messages=[
            {"role": "system", "content": PROFILE_SYSTEM},
            {
                "role": "user",
                "content": user_text,
                "images": [page_image],
            },
        ],
    )

    try:
        profile = parse_json_response(response.content)
    except (json.JSONDecodeError, ValueError):
        profile = _fallback_profile(cache, scale_str, scale_ratio)

    if scale_str and not profile.get("scale"):
        profile["scale"] = scale_str
    if scale_ratio and not profile.get("scale_ratio"):
        profile["scale_ratio"] = scale_ratio

    profile.setdefault("scale", "unknown")
    profile.setdefault("scale_ratio", 100)
    profile.setdefault("confidence", "low")
    profile.setdefault("notes", [])

    return profile


def _fallback_profile(
    cache: ExtractionCache,
    scale_str: Optional[str],
    scale_ratio: Optional[int],
) -> dict:
    """Minimal profile when LLM JSON parse fails."""
    color_info = get_color_summary(cache)
    top_colors = [
        c["color_rgb"] for c in color_info["colors"][:3]
        if c.get("color_rgb")
    ]
    return {
        "drawing_type": "unknown",
        "scale": scale_str or "unknown",
        "scale_ratio": scale_ratio or 100,
        "wall_indicators": {
            "description": "fallback — most frequent non-red line color",
            "likely_colors": top_colors[:1] or [[0, 0, 0]],
            "likely_stroke_range": [0.5, 5.0],
        },
        "grid_indicators": {
            "description": "unknown",
            "likely_colors": [],
            "exclude_from_walls": True,
        },
        "dimension_style": {
            "description": "3-5 digit mm values",
            "location": "unknown",
            "text_pattern": r"\d{3,5}",
        },
        "room_labels": {
            "description": "uppercase room names",
            "examples": [],
        },
        "opening_style": {
            "description": "Dr-/Wn- references",
            "door_pattern": r"Dr-\d+",
            "window_pattern": r"Wn-\d+",
        },
        "confidence": "low",
        "notes": ["Profile LLM parse failed — using fallback heuristics"],
    }
