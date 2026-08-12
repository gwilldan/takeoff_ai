"""
Tool registry — legacy bulk tools + cache query tools.

The profile-first pipeline in extractor/ uses tools/query.py directly
against an ExtractionCache. The bulk tools below remain available for
debugging or ad-hoc agent use but should not be called with full dumps
in an agent loop.
"""

import json
from tools.extract import (
    get_page_metadata,
    get_text_labels,
    get_line_segments,
    get_curves,
    get_line_colors_summary,
)

# ── Function map: name → callable ────────────────────────────────
TOOL_FUNCTIONS = {
    "get_page_metadata":       get_page_metadata,
    "get_text_labels":         get_text_labels,
    "get_line_segments":       get_line_segments,
    "get_curves":              get_curves,
    "get_line_colors_summary": get_line_colors_summary,
}

# ── JSON schemas: what the LLM sees to decide which tool to call ──
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_page_metadata",
            "description": (
                "Get page dimensions and a raw text sample from the title block. "
                "ALWAYS call this first to detect the drawing scale (e.g. 1:100) "
                "before calling any other tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {
                        "type": "string",
                        "description": "Absolute path to the PDF file"
                    }
                },
                "required": ["pdf_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_text_labels",
            "description": (
                "Extract all text content with positions. "
                "Use to find: room names (BEDROOM, KITCHEN etc), "
                "area labels (15 m²), dimension numbers (2750, 3875), "
                "door refs (Dr-01), window refs (Wn-02), scale annotation."
            ),
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
            "description": (
                "Extract all vector line segments with length, color, and stroke width. "
                "Use after get_line_colors_summary so you know which color represents "
                "structural walls vs grid/annotation lines. "
                "Black lines are usually structural, red lines are usually grid."
            ),
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
            "description": (
                "Extract arc and bezier curve segments. "
                "Use to detect door swings (quarter-circle arcs near wall gaps) "
                "and window openings."
            ),
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
            "description": (
                "Get a frequency summary of all colors used in the drawing. "
                "Call this before get_line_segments to understand which color "
                "represents structural elements vs grid/annotation lines."
            ),
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
    """
    Execute a tool by name and return its result as a dict.
    Called by the agent loop — never call this directly from skills.
    """
    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}. Available: {list(TOOL_FUNCTIONS.keys())}"}

    try:
        result = fn(**args)
        return result if isinstance(result, (dict, list)) else {"data": result}
    except Exception as e:
        return {"error": f"Tool {name} failed: {str(e)}"}