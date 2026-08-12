"""
Semantic extraction — uses profile + query tools + targeted LLM calls.

Bulk geometry never enters LLM context. Each step queries narrow slices
from the cache and optionally uses vision tiles for room boundaries.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from config import LLM_VISION_MODEL, ROOM_TILE_SIZE_PTS
from extractor.cache import ExtractionCache
from extractor.llm import TokenTracker, chat_with_tracking, parse_json_response
from extractor.render import render_crop_base64
from extractor.spatial import colors_close, distance
from tools.query import (
    get_curves_near_point,
    get_lines_by_color,
    get_lines_near_point,
    get_text_by_pattern,
    get_text_near_point,
    is_likely_grid_ref,
)

KNOWN_ROOM_NAMES = re.compile(
    r"^(BEDROOM|SITTING ROOM|LOUNGE|LIVING ROOM|DINING|KITCHEN|TOILET|"
    r"BATHROOM|WC|LOBBY|CORRIDOR|PORCH|ENTRANCE PORCH|STORE|VER\.?|VERANDA|"
    r"BALCONY|GARAGE)$",
    re.IGNORECASE,
)

AREA_LABEL = re.compile(r"(\d+(?:\.\d+)?)\s*(?:m²|m2|sq\.?\s*m)", re.IGNORECASE)


def extract_dimensions(cache: ExtractionCache, profile: dict) -> list[dict]:
    dim_pattern = (
        profile.get("dimension_style", {}).get("text_pattern") or r"\d{3,5}"
    )
    candidates = get_text_by_pattern(cache, f"^{dim_pattern}$", limit=200)

    dimensions = []
    seen_values: set[str] = set()

    for span in candidates:
        raw = span["text"].strip()
        if raw in seen_values:
            continue

        # Find matching cache span for grid-ref check
        cx, cy = span["position_pts"]
        cache_span = next(
            (s for s in cache.text_spans
             if s["text"] == raw and abs(s["cx"] - cx) < 2 and abs(s["cy"] - cy) < 2),
            None,
        )
        if cache_span and is_likely_grid_ref(cache_span, cache):
            continue

        try:
            value_mm = int(float(raw))
        except ValueError:
            continue

        if value_mm < 200:
            continue

        nearby_lines = get_lines_near_point(cache, cx, cy, radius=60, limit=5)
        orientation = _infer_orientation(nearby_lines)

        dimensions.append({
            "value_mm": value_mm,
            "text": raw,
            "position_pts": [cx, cy],
            "orientation": orientation,
            "association_method": "spatial_proximity" if nearby_lines else "text_only",
            "confidence": "high" if nearby_lines else "medium",
        })
        seen_values.add(raw)

    return dimensions


def _infer_orientation(nearby_lines: list[dict]) -> str:
    if not nearby_lines:
        return "unknown"
    line = nearby_lines[0]
    sx, sy = line["start"]
    ex, ey = line["end"]
    if abs(ex - sx) >= abs(ey - sy):
        return "horizontal"
    return "vertical"


def extract_walls(cache: ExtractionCache, profile: dict) -> list[dict]:
    wall_info = profile.get("wall_indicators", {})
    likely_colors = wall_info.get("likely_colors") or [[0, 0, 0]]
    stroke_range = wall_info.get("likely_stroke_range") or [0, 10]
    min_stroke = stroke_range[0] if stroke_range else 0
    max_stroke = stroke_range[1] if len(stroke_range) > 1 else None

    grid_colors = profile.get("grid_indicators", {}).get("likely_colors") or []

    all_lines: list[dict] = []
    seen: set[str] = set()

    for color in likely_colors:
        lines = get_lines_by_color(
            cache, color, min_stroke=min_stroke, max_stroke=max_stroke, limit=500
        )
        for line in lines:
            if any(colors_close(line.get("color_rgb"), gc) for gc in grid_colors):
                continue
            key = f"{line['start']}-{line['end']}"
            if key in seen:
                continue
            seen.add(key)
            all_lines.append(line)

    walls = []
    scale_ratio = profile.get("scale_ratio") or 100

    for i, line in enumerate(all_lines):
        length_pts = line["length_pts"]
        length_mm = round(length_pts * (25.4 / 72) * scale_ratio, 1)
        if length_mm < 100:
            continue
        walls.append({
            "id": f"w_{i:03d}",
            "start_pts": line["start"],
            "end_pts": line["end"],
            "length_pts": length_pts,
            "length_mm": length_mm,
            "color_rgb": line.get("color_rgb"),
            "stroke_width": line.get("stroke_width"),
            "source": "profile_guided_filter",
            "confidence": profile.get("confidence", "medium"),
        })

    return walls


def extract_room_labels(cache: ExtractionCache, profile: dict) -> list[dict]:
    rooms = []
    for span in cache.text_spans:
        name = span["text"].strip().upper()
        if not KNOWN_ROOM_NAMES.match(name):
            continue
        rooms.append({
            "name": name,
            "label_position_pts": [span["cx"], span["cy"]],
            "font_size": span["font_size"],
        })
    return rooms


def extract_openings(cache: ExtractionCache, profile: dict) -> list[dict]:
    opening_style = profile.get("opening_style", {})
    door_pat = opening_style.get("door_pattern") or r"Dr-\d+"
    window_pat = opening_style.get("window_pattern") or r"Wn-\d+"

    openings = []
    for span in cache.text_spans:
        text = span["text"].strip()
        if re.match(door_pat, text, re.IGNORECASE):
            openings.append({
                "kind": "door",
                "reference": text,
                "position_pts": [span["cx"], span["cy"]],
            })
        elif re.match(window_pat, text, re.IGNORECASE):
            openings.append({
                "kind": "window",
                "reference": text,
                "position_pts": [span["cx"], span["cy"]],
            })
    return openings


def extract_area_labels(cache: ExtractionCache) -> dict[tuple[float, float], float]:
    """Map label position → area m² from explicit area annotations."""
    areas: dict[tuple[float, float], float] = {}
    for span in cache.text_spans:
        match = AREA_LABEL.search(span["text"])
        if match:
            areas[(span["cx"], span["cy"])] = float(match.group(1))
    return areas


ROOM_TILE_SYSTEM = """You are analyzing a cropped section of an architectural floor plan.

Identify the room in this crop. Return JSON only:
{
  "room_name": "BEDROOM or KITCHEN etc, or UNKNOWN",
  "has_visible_boundary": true,
  "approx_width_mm": null,
  "approx_depth_mm": null,
  "notes": []
}

If you cannot determine the room, set room_name to UNKNOWN.
Do not invent dimensions — leave null if not clearly readable.
"""


def enrich_rooms_with_tiles(
    cache: ExtractionCache,
    profile: dict,
    room_labels: list[dict],
    tracker: TokenTracker,
    max_tiles: int = 15,
) -> list[dict]:
    """
    For each room label, render a local tile and ask the vision model
    to confirm the room and any readable in-room dimensions.
    """
    scale_ratio = profile.get("scale_ratio") or 100
    area_labels = extract_area_labels(cache)
    enriched = []

    for idx, room in enumerate(room_labels[:max_tiles]):
        cx, cy = room["label_position_pts"]
        tile_b64 = render_crop_base64(
            cache.pdf_path,
            (cx, cy),
            crop_size_pts=ROOM_TILE_SIZE_PTS,
            page_index=cache.page_index,
        )

        nearby_dims = get_text_near_point(cache, cx, cy, radius=120, limit=10)
        dim_numbers = [
            d["text"] for d in nearby_dims
            if re.match(r"^\d{3,5}$", d["text"])
        ]

        response = chat_with_tracking(
            tracker,
            step=f"room_tile_{idx}",
            model=LLM_VISION_MODEL,
            messages=[
                {"role": "system", "content": ROOM_TILE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Room label at center: {room['name']}\n"
                        f"Nearby dimension numbers: {dim_numbers[:6]}\n"
                        "Identify this room and any readable dimensions."
                    ),
                    "images": [tile_b64],
                },
            ],
        )

        try:
            tile_result = parse_json_response(response.content)
        except (json.JSONDecodeError, ValueError):
            tile_result = {"room_name": room["name"], "has_visible_boundary": False}

        # Associate explicit area label if one exists near this room
        area_m2: Optional[float] = None
        area_source = "unknown"
        for (ax, ay), val in area_labels.items():
            if distance(cx, cy, ax, ay) < 150:
                area_m2 = val
                area_source = "labeled"
                break

        # Estimate area from nearby dimension numbers if no label
        dimensions_mm: dict = {}
        if dim_numbers:
            nums = sorted({int(n) for n in dim_numbers[:4]})
            if len(nums) >= 2:
                dimensions_mm = {"width": nums[0], "depth": nums[1]}
                if area_m2 is None:
                    area_m2 = round((nums[0] / 1000) * (nums[1] / 1000), 2)
                    area_source = "computed_from_dimensions"

        nearby_walls = get_lines_near_point(cache, cx, cy, radius=150, limit=8)

        enriched.append({
            "id": f"room_{idx:03d}",
            "name": tile_result.get("room_name") or room["name"],
            "instance": _instance_number(room_labels, idx),
            "label_position_pts": room["label_position_pts"],
            "area_m2": area_m2,
            "area_source": area_source,
            "dimensions_mm": dimensions_mm,
            "nearby_wall_ids": [],
            "nearby_wall_count": len(nearby_walls),
            "boundary_confidence": (
                "high" if tile_result.get("has_visible_boundary") else "medium"
            ),
            "tile_notes": tile_result.get("notes") or [],
        })

    return enriched


def _instance_number(room_labels: list[dict], idx: int) -> int:
    name = room_labels[idx]["name"]
    return sum(1 for i, r in enumerate(room_labels[: idx + 1]) if r["name"] == name)


def disambiguate_duplicate_rooms(
    rooms: list[dict],
    tracker: TokenTracker,
) -> list[dict]:
    """One small LLM call to add location hints for duplicate room names."""
    name_counts: dict[str, int] = {}
    for r in rooms:
        name_counts[r["name"]] = name_counts.get(r["name"], 0) + 1

    duplicates = {n for n, c in name_counts.items() if c > 1}
    if not duplicates:
        return rooms

    summary = [
        {
            "id": r["id"],
            "name": r["name"],
            "position_pts": r["label_position_pts"],
        }
        for r in rooms
        if r["name"] in duplicates
    ]

    response = chat_with_tracking(
        tracker,
        step="room_disambiguation",
        messages=[
            {
                "role": "system",
                "content": (
                    "Given room labels with duplicate names and their PDF positions "
                    "(x increases right, y increases down), return JSON array:\n"
                    '[{"id": "room_001", "display_name": "BEDROOM (north-west)"}]\n'
                    "Use positional hints only — do not invent areas."
                ),
            },
            {"role": "user", "content": json.dumps(summary)},
        ],
    )

    try:
        mappings = parse_json_response(response.content)
        if isinstance(mappings, list):
            id_to_name = {m["id"]: m.get("display_name") for m in mappings}
            for r in rooms:
                if r["id"] in id_to_name and id_to_name[r["id"]]:
                    r["display_name"] = id_to_name[r["id"]]
    except (json.JSONDecodeError, ValueError, KeyError):
        pass

    return rooms
