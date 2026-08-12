from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List
from models import ExtractedTable, ExtractedPage, PdfExtractionResult
from tools import dispatch_tool, TOOL_SCHEMAS

import fitz
import pdfplumber
import ollama
import json

def _normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_tables(pdf_path: Path) -> List[List[ExtractedTable]]:
    table_pages: List[List[ExtractedTable]] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            extracted_tables = []
            for table in page.extract_tables():
                rows = [[_normalize_cell(cell) for cell in row] for row in table]
                extracted_tables.append(ExtractedTable(rows=rows))
            table_pages.append(extracted_tables)

    return table_pages


def extract_pdf(pdf_path: Path) -> PdfExtractionResult:
    table_pages = _extract_tables(pdf_path)
    pages: List[ExtractedPage] = []
    full_text_parts: List[str] = []

    document = fitz.open(str(pdf_path))

    try:
        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()

            if not text:
                text = ""

            if text:
                full_text_parts.append(text)

            pages.append(
                ExtractedPage(
                    pageNumber=index,
                    text=text,
                    tables=table_pages[index - 1] if index - 1 < len(table_pages) else []
                )
            )
    finally:
        document.close()

    return PdfExtractionResult(
        pageCount=len(pages),
        text="\n\n".join(full_text_parts).strip(),
        pages=pages,
        extractedAt=datetime.now(timezone.utc).isoformat()
    )


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


def extract_pdf(pdf_path: str) -> dict:
    """
    Run the LLM agent with tool calling.
    Returns the raw structured extraction dict.
    """


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
            model="gpt-oss:120b-cloud",
            messages=messages,
            tools=TOOL_SCHEMAS
        )

        message = response["message"]
        print("agent message:", message)
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