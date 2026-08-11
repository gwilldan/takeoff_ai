"""
Floor Plan BOQ Extraction Worker
─────────────────────────────────
Architecture:
  1. TOOLS   — LLM actively calls these to pull raw data from the PDF
  2. SKILLS  — Deterministic functions your code calls on the LLM output
  3. AGENT   — Orchestrates tools, then hands off to skills pipeline

Entry point:
  process_floor_plan(pdf_path, project_id) → BOQResult
"""

import json
import math
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


# ══════════════════════════════════════════════════════════════════
# DATA MODELS
# ══════════════════════════════════════════════════════════════════

@dataclass
class Room:
    name: str
    area_m2: Optional[float]
    position_pts: tuple

@dataclass
class Wall:
    start_pts: tuple
    end_pts: tuple
    length_mm: float
    color: Optional[tuple]
    stroke_width: float
    is_structural: bool = False

@dataclass
class Opening:
    kind: str          # "door" | "window"
    reference: str     # "Dr-01", "Wn-02" etc
    position_pts: tuple

@dataclass
class BOQItem:
    item_code: str
    description: str
    unit: str          # m, m2, m3, nr, kg
    quantity: float
    category: str
    confidence: str    # high | medium | low
    approved: bool = False

@dataclass
class BOQResult:
    project_id: str
    scale: str
    rooms: list[Room]
    walls: list[Wall]
    openings: list[Opening]
    boq_items: list[BOQItem]
    raw_extraction: dict
    notes: list[str]


# ══════════════════════════════════════════════════════════════════
# TOOLS — LLM calls these actively during inference
# Each tool is a real function + a JSON schema the model reads
# ══════════════════════════════════════════════════════════════════

def tool_get_page_metadata(pdf_path: str) -> dict:
    """
    Returns page size and raw text sample.
    LLM calls this FIRST to detect scale and drawing title.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    raw_text = page.get_text("text")

    # grab first 800 chars — title block is almost always here
    return {
        "page_width_pts": round(page.rect.width, 1),
        "page_height_pts": round(page.rect.height, 1),
        "text_sample": raw_text[:800].strip()
    }


def tool_get_text_labels(pdf_path: str) -> list:
    """
    Returns all text in the PDF with position and font size.
    LLM uses this to identify room names, dimensions, area labels,
    door/window references, and scale annotations.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    labels = []

    for block in page.get_text("dict")["blocks"]:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                labels.append({
                    "text": text,
                    "x": round(span["bbox"][0], 1),
                    "y": round(span["bbox"][1], 1),
                    "font_size": round(span["size"], 1)
                })

    return labels


def tool_get_line_segments(pdf_path: str) -> list:
    """
    Returns all vector line segments with color and stroke width.
    LLM uses this to understand wall geometry.
    Color helps distinguish structural (black) from grid/annotation (red).
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    lines = []

    for path in page.get_drawings():
        color = path.get("color")
        width = path.get("width", 0)
        for item in path["items"]:
            if item[0] == "l":
                start = (round(item[1].x, 1), round(item[1].y, 1))
                end   = (round(item[2].x, 1), round(item[2].y, 1))
                dx = end[0] - start[0]
                dy = end[1] - start[1]
                length_pts = math.sqrt(dx**2 + dy**2)
                lines.append({
                    "start": start,
                    "end": end,
                    "length_pts": round(length_pts, 1),
                    "color_rgb": color,
                    "stroke_width": round(width, 2)
                })

    return lines


def tool_get_curves(pdf_path: str) -> list:
    """
    Returns arc/curve segments.
    LLM uses this to detect door swings (quarter-circle arcs)
    and window openings.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    curves = []

    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] == "c":
                pts = [p for p in item[1:5] if hasattr(p, "x")]
                curves.append({
                    "points": [(round(p.x, 1), round(p.y, 1)) for p in pts],
                    "color_rgb": path.get("color")
                })

    return curves


def tool_get_line_colors_summary(pdf_path: str) -> dict:
    """
    Returns a summary of unique colors found in the drawing.
    LLM uses this to understand which colors map to which element types
    (e.g. red = grid, black = structural walls).
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    color_counts: dict = {}

    for path in page.get_drawings():
        color = path.get("color")
        key = str(color)
        color_counts[key] = color_counts.get(key, 0) + 1

    return {
        "unique_colors": color_counts,
        "total_paths": sum(color_counts.values())
    }


# Tool registry: name → function
TOOL_FUNCTIONS = {
    "get_page_metadata":       tool_get_page_metadata,
    "get_text_labels":         tool_get_text_labels,
    "get_line_segments":       tool_get_line_segments,
    "get_curves":              tool_get_curves,
    "get_line_colors_summary": tool_get_line_colors_summary,
}

# Tool schemas: what the LLM sees to decide which tool to call
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_page_metadata",
            "description": "Get page dimensions and a raw text sample. ALWAYS call this first to detect drawing scale (e.g. 1:100) before anything else.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string", "description": "Absolute path to the PDF file"}
                },
                "required": ["pdf_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_text_labels",
            "description": "Extract all text with positions. Use to find: room names (BEDROOM, KITCHEN etc), area labels (15 m²), dimension numbers (2750, 3875), door refs (Dr-01), window refs (Wn-02), scale annotation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string"}
                },
                "required": ["pdf_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_line_segments",
            "description": "Extract all vector lines with length, color, and stroke width. Use to identify wall geometry. Black lines are usually structural walls, red lines are usually grid/dimension lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string"}
                },
                "required": ["pdf_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_curves",
            "description": "Extract arc and curve segments. Use to detect door swings (quarter-circle arcs) and window openings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string"}
                },
                "required": ["pdf_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_line_colors_summary",
            "description": "Get a summary of all colors used in the drawing and how many times each appears. Use this when you need to understand which color represents structural vs annotation elements.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string"}
                },
                "required": ["pdf_path"]
            }
        }
    }
]


def dispatch_tool(name: str, args: dict) -> dict:
    """Execute a tool call and return its result."""
    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    try:
        result = fn(**args)
        return result if isinstance(result, dict) else {"data": result}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
# LLM AGENT — uses tools to extract structured data from the PDF
# Returns a structured dict the skills pipeline consumes
# ══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
You are an architectural drawing analyst specialising in construction quantity surveying.

Your job is to extract structured data from a PDF floor plan using the available tools.

REQUIRED PROCESS:
1. Call get_page_metadata first — detect the drawing scale (e.g. 1:100) from the text sample
2. Call get_text_labels — find room names, area labels (m²), dimension numbers, door/window refs
3. Call get_line_colors_summary — understand which colors are structural vs annotation
4. Call get_line_segments — get wall geometry (filter to structural color only)
5. Call get_curves if needed — detect door swings and window arcs

RETURN a single JSON object (no markdown, no explanation) matching exactly this schema:
{
  "scale": "1:100",
  "scale_ratio": 100,
  "structural_color": [r, g, b],
  "rooms": [
    {"name": "BEDROOM", "area_m2": 15, "position_pts": [x, y]}
  ],
  "dimension_values_mm": [2750, 3875, 16850],
  "door_references": ["Dr-01", "Dr-02"],
  "window_references": ["Wn-01", "Wn-02"],
  "structural_lines": [
    {"start": [x1, y1], "end": [x2, y2], "length_pts": 123.4}
  ],
  "confidence": "high | medium | low",
  "notes": ["any ambiguities or assumptions made"]
}

RULES:
- Only include structural (wall) lines, exclude red grid lines and dimension lines
- dimension_values_mm: only include numbers that are clearly dimension annotations (>200), exclude grid ref numbers (1-14)
- If scale is not found in the text, set scale to "unknown" and note it
- Return ONLY the JSON, nothing else
"""


def run_agent(pdf_path: str) -> dict:
    """
    Run the LLM agent with tool calling.
    Returns the raw structured extraction dict.
    """
    try:
        import ollama
    except ImportError:
        raise RuntimeError("ollama package not installed. Run: pip install ollama")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Extract all structured data from this floor plan PDF: {pdf_path}"
        }
    ]

    print("[agent] Starting extraction agent...")
    max_iterations = 10  # prevent infinite tool loops
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        response = ollama.chat(
            model="qwen2.5",
            messages=messages,
            tools=TOOL_SCHEMAS
        )

        message = response["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            # Model returned final answer
            content = message.get("content", "")
            print(f"[agent] Agent finished after {iteration} iterations")

            # Strip markdown fences if model added them
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                print(f"[agent] JSON parse error: {e}")
                print(f"[agent] Raw content: {content[:300]}")
                return {"error": "Failed to parse agent output", "raw": content}

        # Execute each tool the model requested
        for tool_call in tool_calls:
            fn_name = tool_call["function"]["name"]
            fn_args = tool_call["function"]["arguments"]
            print(f"[agent] Tool call: {fn_name}()")

            result = dispatch_tool(fn_name, fn_args)

            # Return tool result to model
            messages.append({
                "role": "tool",
                "content": json.dumps(result)
            })

    return {"error": "Agent exceeded max iterations without returning a result"}


# ══════════════════════════════════════════════════════════════════
# SKILLS — deterministic, called by your code, never by the model
# ══════════════════════════════════════════════════════════════════

PDF_POINTS_PER_INCH = 72
MM_PER_INCH = 25.4


def skill_compute_measurements(
    structural_lines: list,
    scale_ratio: int
) -> list[Wall]:
    """
    SKILL: Convert PDF point coordinates to real-world mm measurements.
    Pure math — no LLM involvement.
    """
    pts_to_mm = (MM_PER_INCH / PDF_POINTS_PER_INCH) * scale_ratio

    walls = []
    for line in structural_lines:
        start = tuple(line["start"])
        end   = tuple(line["end"])
        length_mm = round(line["length_pts"] * pts_to_mm, 1)

        # Skip lines shorter than 100mm real-world (likely annotation artifacts)
        if length_mm < 100:
            continue

        walls.append(Wall(
            start_pts=start,
            end_pts=end,
            length_mm=length_mm,
            color=None,
            stroke_width=0,
            is_structural=True
        ))

    return walls


def skill_classify_rooms(raw_rooms: list) -> list[Room]:
    """
    SKILL: Convert raw room dicts from agent output into typed Room objects.
    Validates area values and normalises room names.
    No LLM — pure data transformation.
    """
    rooms = []
    for r in raw_rooms:
        name = r.get("name", "UNKNOWN").strip().upper()
        area = r.get("area_m2")

        # Sanity check: areas should be positive and realistic
        if area is not None:
            if not (0.5 <= area <= 500):
                print(f"[skill_classify_rooms] Suspicious area {area} for {name}, flagging")
                area = None  # nulled, human reviewer must fill

        rooms.append(Room(
            name=name,
            area_m2=area,
            position_pts=tuple(r.get("position_pts", [0, 0]))
        ))

    return rooms


def skill_classify_openings(
    door_refs: list[str],
    window_refs: list[str]
) -> list[Opening]:
    """
    SKILL: Convert door/window reference strings into Opening objects.
    In a full implementation you'd also cross-reference position data.
    """
    openings = []

    for ref in door_refs:
        openings.append(Opening(
            kind="door",
            reference=ref.strip(),
            position_pts=(0, 0)  # position resolved in future from curve data
        ))

    for ref in window_refs:
        openings.append(Opening(
            kind="window",
            reference=ref.strip(),
            position_pts=(0, 0)
        ))

    return openings


# BOQ category rules: room name → BOQ work section
ROOM_BOQ_CATEGORIES = {
    "BEDROOM":        "F — Masonry",
    "SITTING ROOM":   "F — Masonry",
    "KITCHEN":        "F — Masonry",
    "TOILET":         "F — Masonry",
    "BATHROOM":       "F — Masonry",
    "LOBBY":          "F — Masonry",
    "PORCH":          "F — Masonry",
    "ENTRANCE PORCH": "F — Masonry",
    "VER.":           "Q — Paving / Flooring",
}

ITEM_CODE_SEQUENCE = {}  # tracks item codes per category for numbering


def _next_item_code(category: str) -> str:
    prefix = category.split("—")[0].strip()
    ITEM_CODE_SEQUENCE[prefix] = ITEM_CODE_SEQUENCE.get(prefix, 0) + 1
    return f"{prefix}{ITEM_CODE_SEQUENCE[prefix]:02d}"


def skill_takeoff(
    walls: list[Wall],
    rooms: list[Room],
    openings: list[Opening],
    wall_height_mm: float = 3000.0  # default floor-to-ceiling height
) -> list[BOQItem]:
    """
    SKILL: Apply POMI-style quantity takeoff rules to produce BOQ items.

    Rules applied:
    - Blockwork: net wall area (gross - openings deductions)
    - Finishes: floor area per room
    - Doors/windows: counted by reference type

    This is the deterministic core — measurements are arithmetic only.
    """
    ITEM_CODE_SEQUENCE.clear()
    boq: list[BOQItem] = []

    # ── 1. Blockwork / masonry from wall geometry ───────────────
    total_wall_length_mm = sum(w.length_mm for w in walls if w.is_structural)
    total_gross_area_m2 = round(
        (total_wall_length_mm * wall_height_mm) / 1e6, 3
    )

    # Deduct standard opening sizes (POMI rule: deduct openings > 0.5m²)
    door_count    = sum(1 for o in openings if o.kind == "door")
    window_count  = sum(1 for o in openings if o.kind == "window")

    # Standard assumed sizes (real project would use actual schedule dimensions)
    std_door_area_m2   = 0.9 * 2.1   # 900mm × 2100mm
    std_window_area_m2 = 1.2 * 1.2   # 1200mm × 1200mm

    total_openings_area_m2 = round(
        (door_count * std_door_area_m2) + (window_count * std_window_area_m2), 3
    )
    net_wall_area_m2 = round(total_gross_area_m2 - total_openings_area_m2, 3)

    boq.append(BOQItem(
        item_code=_next_item_code("F"),
        description=(
            f"200mm sandcrete blockwork in walls; "
            f"cement rendered and plastered both faces; "
            f"in superstructure generally as per drawing. "
            f"(Gross: {total_gross_area_m2}m², Deductions: {total_openings_area_m2}m²)"
        ),
        unit="m2",
        quantity=net_wall_area_m2,
        category="F — Masonry",
        confidence="medium" if net_wall_area_m2 > 0 else "low"
    ))

    # ── 2. Floor finishes per room ───────────────────────────────
    for room in rooms:
        if room.area_m2 is None:
            continue

        category = ROOM_BOQ_CATEGORIES.get(room.name, "Q — Paving / Flooring")
        boq.append(BOQItem(
            item_code=_next_item_code("Q"),
            description=(
                f"Floor finishes to {room.name}; "
                f"600×600mm ceramic tiles on screed bed; "
                f"grouted joints as specification."
            ),
            unit="m2",
            quantity=room.area_m2,
            category="Q — Paving / Flooring",
            confidence="high" if room.area_m2 else "low"
        ))

    # ── 3. Doors — counted, not measured ────────────────────────
    # Group by reference type (Dr-01, Dr-02, Dr-03)
    door_type_counts: dict[str, int] = {}
    for o in openings:
        if o.kind == "door":
            door_type_counts[o.reference] = door_type_counts.get(o.reference, 0) + 1

    for ref, count in door_type_counts.items():
        boq.append(BOQItem(
            item_code=_next_item_code("L"),
            description=(
                f"Supply and fix door type {ref}; "
                f"flush panel door on hardwood frame; "
                f"complete with ironmongery as schedule."
            ),
            unit="nr",
            quantity=count,
            category="L — Windows / Doors",
            confidence="high"
        ))

    # ── 4. Windows — counted, not measured ──────────────────────
    window_type_counts: dict[str, int] = {}
    for o in openings:
        if o.kind == "window":
            window_type_counts[o.reference] = window_type_counts.get(o.reference, 0) + 1

    for ref, count in window_type_counts.items():
        boq.append(BOQItem(
            item_code=_next_item_code("L"),
            description=(
                f"Supply and fix window type {ref}; "
                f"aluminium sliding window; "
                f"complete with ironmongery as schedule."
            ),
            unit="nr",
            quantity=count,
            category="L — Windows / Doors",
            confidence="high"
        ))

    return boq


def skill_generate_boq_descriptions(boq_items: list[BOQItem]) -> list[BOQItem]:
    """
    SKILL: (Optional second LLM pass)
    Rewrites BOQ item descriptions into properly worded, standard-compliant
    phrasing. This is the ONE place a second LLM call adds real value —
    description wording is language work, not math.

    Skipped if ollama is unavailable — raw descriptions are used instead.
    """
    try:
        import ollama

        items_json = json.dumps([
            {"item_code": i.item_code, "description": i.description,
             "unit": i.unit, "quantity": i.quantity}
            for i in boq_items
        ], indent=2)

        response = ollama.chat(
            model="qwen2.5",
            messages=[{
                "role": "user",
                "content": (
                    f"Rewrite these BOQ item descriptions to follow standard "
                    f"POMI/NRM2 wording conventions for Nigerian construction projects. "
                    f"Keep item_code, unit, and quantity exactly as given. "
                    f"Return ONLY a JSON array, no explanation.\n\n{items_json}"
                )
            }]
        )

        content = response["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        rewritten = json.loads(content.strip())

        for item, rewrite in zip(boq_items, rewritten):
            item.description = rewrite.get("description", item.description)

    except Exception as e:
        print(f"[skill_generate_boq_descriptions] Skipped: {e}")

    return boq_items


# ══════════════════════════════════════════════════════════════════
# ORCHESTRATOR — the actual worker entry point
# This is what your job queue calls
# ══════════════════════════════════════════════════════════════════

def process_floor_plan(pdf_path: str, project_id: str) -> BOQResult:
    """
    Main entry point for the worker.
    Called by the job queue consumer (BullMQ / Celery / raw Redis).

    Returns a BOQResult with all extracted and computed data.
    Raises on unrecoverable errors so the queue can retry.
    """
    pdf_path = str(pdf_path)
    notes: list[str] = []

    print(f"\n{'='*60}")
    print(f"[worker] Processing project: {project_id}")
    print(f"[worker] PDF: {pdf_path}")
    print(f"{'='*60}\n")

    # ── STAGE 1: Agent uses tools to extract raw data ────────────
    print("[worker] Stage 1: Running LLM agent with tools...")
    raw = run_agent(pdf_path)

    if "error" in raw:
        raise RuntimeError(f"Agent extraction failed: {raw['error']}")

    print(f"[worker] Agent returned: scale={raw.get('scale')}, "
          f"rooms={len(raw.get('rooms', []))}, "
          f"lines={len(raw.get('structural_lines', []))}")

    notes.extend(raw.get("notes", []))

    # ── STAGE 2: Skill — compute real-world measurements ─────────
    print("[worker] Stage 2: Computing measurements (skill)...")
    scale_ratio = raw.get("scale_ratio", 100)

    if scale_ratio == 0 or raw.get("scale") == "unknown":
        notes.append("WARNING: Scale not detected. Measurements may be incorrect. Manual scale confirmation required.")
        scale_ratio = 100  # fallback assumption

    walls = skill_compute_measurements(
        structural_lines=raw.get("structural_lines", []),
        scale_ratio=scale_ratio
    )
    print(f"[worker] Walls after measurement skill: {len(walls)}")

    # ── STAGE 3: Skill — classify rooms ──────────────────────────
    print("[worker] Stage 3: Classifying rooms (skill)...")
    rooms = skill_classify_rooms(raw.get("rooms", []))

    # Flag any rooms with missing areas for human review
    missing_area = [r.name for r in rooms if r.area_m2 is None]
    if missing_area:
        notes.append(f"Rooms with unresolved areas (manual input needed): {missing_area}")

    # ── STAGE 4: Skill — classify openings ───────────────────────
    print("[worker] Stage 4: Classifying openings (skill)...")
    openings = skill_classify_openings(
        door_refs=raw.get("door_references", []),
        window_refs=raw.get("window_references", [])
    )

    # ── STAGE 5: Skill — quantity takeoff ────────────────────────
    print("[worker] Stage 5: Running quantity takeoff (skill)...")
    boq_items = skill_takeoff(walls, rooms, openings)

    # ── STAGE 6: Optional second LLM pass — description wording ──
    print("[worker] Stage 6: Refining BOQ descriptions (LLM)...")
    boq_items = skill_generate_boq_descriptions(boq_items)

    result = BOQResult(
        project_id=project_id,
        scale=raw.get("scale", "unknown"),
        rooms=rooms,
        walls=walls,
        openings=openings,
        boq_items=boq_items,
        raw_extraction=raw,
        notes=notes
    )

    print(f"\n[worker] Done. BOQ items: {len(boq_items)}")
    print(f"[worker] Notes: {notes}\n")

    return result


# ══════════════════════════════════════════════════════════════════
# QUEUE CONSUMER — replaces this with BullMQ/Celery in production
# This is the loop that picks up jobs from Redis and processes them
# ══════════════════════════════════════════════════════════════════

def start_worker():
    """
    Production queue consumer.
    In real deployment: replace redis_client.brpop with your queue client.
    Pattern is identical for BullMQ (Python), Celery, or raw Redis.
    """
    try:
        import redis
        r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=6379, decode_responses=True)
        print("[worker] Listening for jobs on 'extraction_jobs'...")

        while True:
            # Block until a job arrives (brpop = blocking right-pop from list)
            _, payload = r.brpop("extraction_jobs")
            job = json.loads(payload)

            project_id = job["projectId"]
            pdf_path   = job["pdfPath"]

            try:
                result = process_floor_plan(pdf_path, project_id)

                # In production: save result to PostgreSQL here
                # db.save_boq_result(result)
                # db.update_project_status(project_id, "complete")

                print(f"[worker] Project {project_id} complete")
                print(json.dumps(asdict(result), indent=2, default=str))

            except Exception as e:
                print(f"[worker] Project {project_id} failed: {e}")
                # db.update_project_status(project_id, "failed")
                # db.log_error(project_id, str(e))

    except ImportError:
        print("[worker] redis package not installed. Run: pip install redis")


# ══════════════════════════════════════════════════════════════════
# CLI — for testing a single PDF directly without the queue
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python main.py <path-to-pdf> [project-id]")
        print("       python main.py --worker   (start queue consumer)")
        sys.exit(1)

    if sys.argv[1] == "--worker":
        start_worker()
    else:
        pdf   = sys.argv[1]
        pid   = sys.argv[2] if len(sys.argv) > 2 else "test-project-001"
        result = process_floor_plan(pdf, pid)

        print("\n" + "="*60)
        print("FINAL BOQ RESULT")
        print("="*60)
        print(json.dumps(asdict(result), indent=2, default=str))