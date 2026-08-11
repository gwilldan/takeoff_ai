"""
Extraction tools — functions the LLM agent actively calls during inference.

Rules:
- Every function here reads from the PDF only, no computation, no math
- Return plain dicts/lists (JSON-serialisable), not dataclass instances
- Keep each function focused on one element type
- Never call these directly from skills or renderers
"""

import math
import fitz  # PyMuPDF


def get_page_metadata(pdf_path: str) -> dict:
    """
    Returns page dimensions and a raw text sample from the first page.

    LLM uses this to:
    - Detect drawing scale (e.g. "1:100") from the title block
    - Understand page size for spatial reasoning
    - Find the drawing title and author

    Always call this first before any other tool.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    raw_text = page.get_text("text")

    return {
        "page_width_pts": round(page.rect.width, 1),
        "page_height_pts": round(page.rect.height, 1),
        # First 800 chars almost always contains the title block
        "text_sample": raw_text[:800].strip(),
        "total_text_length": len(raw_text)
    }


def get_text_labels(pdf_path: str) -> list:
    """
    Returns all text content in the PDF with position and font size.

    LLM uses this to identify:
    - Room names (BEDROOM, KITCHEN, TOILET, SITTING ROOM etc)
    - Area labels (15 m², 21 m²)
    - Dimension numbers (2750, 3875, 16850)
    - Door references (Dr-01, Dr-02, Dr-03)
    - Window references (Wn-01, Wn-02 etc)
    - Scale annotation (1:100)

    Position data (x, y) allows the LLM to spatially associate
    labels with nearby geometry.
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


def get_line_segments(pdf_path: str) -> list:
    """
    Returns all straight vector line segments with position, length,
    color, and stroke width.

    LLM uses this to:
    - Identify structural wall lines (typically black, thicker)
    - Exclude grid/dimension lines (typically red or thin)
    - Understand wall layout and room boundaries

    color_rgb is a [r, g, b] tuple where each value is 0.0-1.0.
    Red lines are usually grid lines, black lines are usually walls.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    lines = []

    for path in page.get_drawings():
        color = path.get("color")
        width = path.get("width", 0)

        for item in path["items"]:
            if item[0] != "l":
                continue

            start = (round(item[1].x, 1), round(item[1].y, 1))
            end   = (round(item[2].x, 1), round(item[2].y, 1))
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            length_pts = math.sqrt(dx**2 + dy**2)

            lines.append({
                "start": start,
                "end": end,
                "length_pts": round(length_pts, 1),
                "color_rgb": list(color) if color else None,
                "stroke_width": round(width, 2)
            })

    return lines


def get_curves(pdf_path: str) -> list:
    """
    Returns all bezier curve / arc segments in the drawing.

    LLM uses this to detect:
    - Door swings (quarter-circle arcs near wall gaps)
    - Window openings (arc-like shapes on wall lines)

    Note: In PDFs, circles and arcs decompose into multiple bezier
    curve segments. A door swing is typically 3-4 curve segments
    forming a quarter circle near a wall opening.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    curves = []

    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] != "c":
                continue

            pts = [p for p in item[1:5] if hasattr(p, "x")]
            if not pts:
                continue

            curves.append({
                "points": [(round(p.x, 1), round(p.y, 1)) for p in pts],
                "color_rgb": list(path.get("color")) if path.get("color") else None
            })

    return curves


def get_line_colors_summary(pdf_path: str) -> dict:
    """
    Returns a frequency summary of all colors used across drawing paths.

    LLM uses this to understand color conventions in this specific drawing:
    - Which color appears most = likely the primary structural color
    - Red (high r, low g, low b) = usually grid lines or dimension lines
    - Black (0, 0, 0) or near-black = usually structural walls

    Useful before calling get_line_segments so the LLM knows which
    color to filter for when identifying walls.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    color_counts: dict = {}

    for path in page.get_drawings():
        color = path.get("color")
        key = str(color)
        color_counts[key] = color_counts.get(key, 0) + 1

    return {
        "color_frequency": color_counts,
        "total_paths": sum(color_counts.values()),
        "hint": "High r, low g, low b = likely red (grid). Near [0,0,0] = likely black (structural walls)."
    }