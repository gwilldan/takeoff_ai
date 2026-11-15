"""
Page classification — is this page a construction plan, and at what scale?

A drawing set is mostly not plans: cover sheets, schedules, specifications,
revision tables. A free deterministic pre-filter drops pages with nothing
drawn on them, and only survivors cost a vision call.
"""

from __future__ import annotations

import json
import logging
import re

from app.config import settings
from app.extractor.cache import ExtractionCache
from app.extractor.llm import TokenTracker, chat_with_tracking, parse_json_response
from app.extractor.render import render_page_base64

log = logging.getLogger(__name__)

# A plan is drawn: enough vector strokes, or an embedded image big enough to be the drawing.
MIN_LINE_SEGMENTS = 40
MIN_IMAGE_COVERAGE = 0.2

PLAN_KEYWORDS = re.compile(
    r"\b(PLAN|FLOOR|LAYOUT|SECTION|ELEVATION|SITE|BEDROOM|KITCHEN|TOILET|"
    r"BATHROOM|LOBBY|SITTING|LOUNGE|VERANDA|PORCH|STORE|SCALE)\b", re.IGNORECASE,
)
NON_PLAN_TITLES = re.compile(
    r"\b(SCHEDULE|SPECIFICATION|BILL OF QUANTITIES|REVISION HISTORY|"
    r"DRAWING (LIST|REGISTER)|CONTENTS|NOTES? SHEET|LEGEND SHEET)\b", re.IGNORECASE,
)
SCALE = re.compile(r"1\s*:\s*(\d+)")

# Only these are measurable in plan. Anything else the model returns is skipped
# even when it also says is_plan — a section is a drawing, not a plan.
PLAN_TYPES = {"ground_floor_plan", "upper_floor_plan", "floor_plan", "site_plan", "roof_plan", "setting_out_plan", "unknown"}

# Sections and elevations carry a column of datum levels (AGL, DPC, FFL …);
# plans do not. Used by the prefilter and as the fallback's judgement.
SECTION_TITLE = re.compile(r"\b(SECTION|ELEVATION|DETAIL)S?\b", re.IGNORECASE)
LEVEL_MARK = re.compile(r"\b(AGL|DPC|FFL|SSL|SFL|RL|NGL)\b")
MIN_LEVEL_MARKS = 3

CLASSIFY_SYSTEM = """You are an architectural drawing analyst.

You receive one page from a construction drawing set: an image plus a short
summary of its content. Decide whether it is a construction plan that can be
measured, and read its scale.

A construction plan is a scaled orthographic drawing of a building or site seen
from above: floor plans, site plans, setting-out plans, roof plans. These are
NOT plans: sections, elevations, details, door and window schedules,
specifications, bills of quantities, revision tables, drawing registers, title
or cover sheets, 3D views, photographs.

Return a single JSON object, no markdown:
{
  "is_plan": true,
  "plan_type": "ground_floor_plan | upper_floor_plan | site_plan | roof_plan | section | elevation | detail | schedule | cover | other",
  "not_plan_reason": "only when is_plan is false",
  "scale": "1:100 or unknown",
  "scale_ratio": 100,
  "confidence": "high | medium | low",
  "notes": ["assumptions or ambiguities"]
}
"""


def detect_scale(text: str) -> tuple[str | None, int | None]:
    match = SCALE.search(text)
    return (f"1:{match.group(1)}", int(match.group(1))) if match else (None, None)


def prefilter_page(cache: ExtractionCache) -> str | None:
    """Reason to skip the page without a model call, or None when it is worth one."""
    if len(cache.line_segments) < MIN_LINE_SEGMENTS and cache.image_coverage < MIN_IMAGE_COVERAGE:
        return f"only {len(cache.line_segments)} line segments and no drawing image — nothing to measure"
    heading = cache.title_block_text[:400]
    if NON_PLAN_TITLES.search(heading) and not PLAN_KEYWORDS.search(heading):
        return "title reads as a schedule or specification, not a drawing"
    if looks_like_section(cache):
        return "reads as a section or elevation, which cannot be measured in plan"
    return None


def looks_like_section(cache: ExtractionCache) -> bool:
    titled = SECTION_TITLE.search(cache.title_block_text) and not re.search(r"\bPLAN\b", cache.title_block_text, re.I)
    return bool(titled) or len(LEVEL_MARK.findall(cache.full_text)) >= MIN_LEVEL_MARKS


def classify_page(cache: ExtractionCache, tracker: TokenTracker) -> dict:
    """
    Classify one page with a single vision call.

    Falls back to "plan at the detected scale" when the model is unreachable or
    returns bad JSON — a page is never dropped because of a bad response.
    """
    if reason := prefilter_page(cache):
        return {"is_plan": False, "plan_type": "other", "not_plan_reason": reason,
                "classified_by": "prefilter", "confidence": "high", "notes": []}

    # The whole text, not the title block: "1 : 100" is often the last thing on the sheet.
    scale_text, scale_ratio = detect_scale(cache.full_text)
    summary = cache.to_summary()
    prompt = (
        f"Classify this drawing page.\n\nPage {cache.page_number}\n"
        f"Title block sample:\n{summary['title_block_text'][:600]}\n\n"
        f"Text spans: {summary['text_span_count']}, line segments: {summary['line_segment_count']}, "
        f"image coverage: {summary['image_coverage']}\n"
        f"Sample labels: {summary['sample_text_labels'][:20]}\n"
        + (f"Scale found in the text: {scale_text}\n" if scale_text else "")
    )

    try:
        response = chat_with_tracking(
            tracker, step=f"classify_page_{cache.page_number}", model=settings.llm_vision_model,
            messages=[
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": prompt, "images": [render_page_base64(cache.pdf_path, cache.page_index)]},
            ],
        )
        profile = {**parse_json_response(response.content), "classified_by": "vision"}
    except Exception as error:  # network, auth, or malformed JSON
        log.warning("page %d: classification fell back to heuristics: %s", cache.page_number, error)
        profile = {"is_plan": True, "plan_type": "unknown", "classified_by": "fallback", "confidence": "low",
                   "notes": [f"Classification fell back to heuristics: {error}"]}

    if scale_text and profile.get("scale") in (None, "", "unknown"):
        profile["scale"] = scale_text
    if scale_ratio and not profile.get("scale_ratio"):
        profile["scale_ratio"] = scale_ratio

    for key, default in (("is_plan", False), ("plan_type", "other"), ("scale", "unknown"),
                         ("scale_ratio", 100), ("confidence", "low"), ("notes", [])):
        profile.setdefault(key, default)

    if profile["is_plan"] and profile["plan_type"] not in PLAN_TYPES:
        profile["is_plan"] = False
        profile["not_plan_reason"] = f"{profile['plan_type'].replace('_', ' ')} cannot be measured in plan"
    return profile
