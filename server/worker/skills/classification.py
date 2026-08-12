"""
Classification skill — converts raw agent output into typed model objects.

Validates extracted data, flags suspicious values, and maps
room names to BOQ work sections.

Deterministic rule-based logic only — no LLM involvement.
"""

from models import Room, Opening


# ── Room name → POMI/NRM2 work section mapping ───────────────────
ROOM_BOQ_CATEGORY = {
    "BEDROOM":        "F — Masonry",
    "SITTING ROOM":   "F — Masonry",
    "LOUNGE":         "F — Masonry",
    "LIVING ROOM":    "F — Masonry",
    "DINING":         "F — Masonry",
    "KITCHEN":        "F — Masonry",
    "TOILET":         "F — Masonry",
    "BATHROOM":       "F — Masonry",
    "WC":             "F — Masonry",
    "LOBBY":          "F — Masonry",
    "CORRIDOR":       "F — Masonry",
    "PORCH":          "F — Masonry",
    "ENTRANCE PORCH": "F — Masonry",
    "STORE":          "F — Masonry",
    "VER.":           "Q — Paving / Flooring",
    "VERANDA":        "Q — Paving / Flooring",
    "BALCONY":        "Q — Paving / Flooring",
    "GARAGE":         "Q — Paving / Flooring",
}

# Sanity bounds for room areas
MIN_ROOM_AREA_M2 = 0.5    # smaller than this is almost certainly a parsing error
MAX_ROOM_AREA_M2 = 500.0  # larger than this is suspicious for a typical residential plan


def classify_rooms(raw_rooms: list) -> tuple[list[Room], list[str]]:
    """
    Convert raw room dicts from agent output into typed Room objects.

    Validates area values and flags suspicious ones.
    Returns (rooms, warning_notes) — notes are added to BOQResult.notes.
    """
    rooms: list[Room] = []
    notes: list[str] = []

    for r in raw_rooms:
        name = r.get("name", "UNKNOWN").strip().upper()
        area = r.get("area_m2")

        if area is not None:
            try:
                area = float(area)
                if not (MIN_ROOM_AREA_M2 <= area <= MAX_ROOM_AREA_M2):
                    notes.append(
                        f"Suspicious area {area}m² for {name} — nulled, manual input required"
                    )
                    area = None
            except (TypeError, ValueError):
                notes.append(f"Non-numeric area value for {name} — nulled")
                area = None

        rooms.append(Room(
            name=name,
            area_m2=area,
            position_pts=tuple(r.get("position_pts", [0, 0]))
        ))

    missing = [r.name for r in rooms if r.area_m2 is None]
    if missing:
        notes.append(f"Rooms with missing areas (human input needed): {missing}")

    return rooms, notes


def classify_openings(
    door_refs: list[str],
    window_refs: list[str]
) -> list[Opening]:
    """
    Convert door/window reference strings into typed Opening objects.

    In v2: cross-reference with curve positions to get spatial location.
    Currently position is (0, 0) as a placeholder.
    """
    openings: list[Opening] = []

    for ref in door_refs:
        openings.append(Opening(
            kind="door",
            reference=ref.strip(),
            position_pts=(0, 0)
        ))

    for ref in window_refs:
        openings.append(Opening(
            kind="window",
            reference=ref.strip(),
            position_pts=(0, 0)
        ))

    return openings


def get_room_category(room_name: str) -> str:
    """Map a room name to its BOQ work section. Defaults to masonry."""
    return ROOM_BOQ_CATEGORY.get(room_name.upper(), "F — Masonry")