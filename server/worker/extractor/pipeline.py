"""
Extraction pipeline — orchestrates parse → profile → semantics → output.

Entry point: extract_pdf(pdf_path) → structured dict with token_usage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import EXTRACTION_CACHE_DIR
from extractor.cache import parse_pdf_to_cache
from extractor.llm import TokenTracker
from extractor.pdf_utils import PdfPageError
from extractor.profile import infer_drawing_profile
from extractor.semantics import (
    disambiguate_duplicate_rooms,
    enrich_rooms_with_tiles,
    extract_dimensions,
    extract_openings,
    extract_room_labels,
    extract_walls,
)


def extract_pdf(pdf_path: str, *, write_cache: bool = True) -> dict:
    """
    Full extraction pipeline for one architectural floor plan PDF.

    Stages:
      1. Parse PDF to cache (deterministic, no LLM)
      2. Infer per-document drawing profile (1 vision LLM call)
      3. Extract dimensions, walls, rooms, openings (query tools + tile vision)
      4. Disambiguate duplicate room names (optional small LLM call)

    Returns structured extraction dict including token_usage breakdown.
    """
    tracker = TokenTracker()
    pdf_path = str(Path(pdf_path).resolve())

    print(f"[pipeline] Parsing PDF: {pdf_path}")
    try:
        cache = parse_pdf_to_cache(pdf_path)
    except PdfPageError as exc:
        raise ValueError(str(exc)) from exc

    if write_cache:
        cache_path = Path(EXTRACTION_CACHE_DIR) / f"{Path(pdf_path).stem}.json"
        cache.write_json(cache_path)
        print(f"[pipeline] Cache written to {cache_path}")

    print("[pipeline] Inferring drawing profile...")
    profile = infer_drawing_profile(cache, tracker)
    print("""[pipeline]-response: first LLM response
    profile:
    """, profile)

    print("[pipeline] Extracting dimensions...")
    dimensions = extract_dimensions(cache, profile)

    print("[pipeline] Extracting walls...")
    walls = extract_walls(cache, profile)

    print("[pipeline] Extracting room labels...")
    room_labels = extract_room_labels(cache, profile)

    print(f"[pipeline] Enriching {len(room_labels)} rooms with vision tiles...")
    rooms = enrich_rooms_with_tiles(cache, profile, room_labels, tracker)

    print("[pipeline] Disambiguating duplicate room names...")
    rooms = disambiguate_duplicate_rooms(rooms, tracker)

    print("[pipeline] Extracting openings...")
    openings = extract_openings(cache, profile)

    notes = list(profile.get("notes") or [])
    if profile.get("confidence") == "low":
        notes.append("Drawing profile confidence is low — verify wall and dimension extraction")

    duplicate_names = {r["name"] for r in rooms}
    if len(duplicate_names) < len(rooms):
        notes.append("Duplicate room names present — display_name hints added where possible")

    result = {
        "scale": profile.get("scale", "unknown"),
        "scale_ratio": profile.get("scale_ratio", 100),
        "drawing_profile": profile,
        "metadata": {
            "pdf_path": pdf_path,
            "page_size_pts": [cache.page_width_pts, cache.page_height_pts],
            "text_span_count": len(cache.text_spans),
            "line_segment_count": len(cache.line_segments),
            "curve_count": len(cache.curves),
        },
        "walls": walls,
        "dimensions": dimensions,
        "rooms": rooms,
        "openings": openings,
        "grid": {
            "note": "Grid refs excluded from dimensions via spatial heuristics",
        },
        "notes": notes,
        "confidence": profile.get("confidence", "medium"),
        "token_usage": tracker.to_dict(),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }

    print(
        f"[pipeline] Done. "
        f"walls={len(walls)} dimensions={len(dimensions)} "
        f"rooms={len(rooms)} openings={len(openings)} "
        f"total_tokens={tracker.total_tokens}"
    )

    return result
