"""
Parse a PDF into an in-memory cache — no style assumptions, no LLM.

All raw vectors and text are stored here. Downstream query tools and the
profile step read slices from this cache instead of re-parsing the PDF.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from extractor.pdf_utils import load_page, open_pdf_document, resolve_page_index


@dataclass
class ExtractionCache:
    pdf_path: str
    page_index: int
    page_width_pts: float
    page_height_pts: float
    title_block_text: str
    text_spans: list[dict] = field(default_factory=list)
    line_segments: list[dict] = field(default_factory=list)
    curves: list[dict] = field(default_factory=list)
    color_summary: dict[str, int] = field(default_factory=dict)
    full_text: str = ""

    def to_summary(self) -> dict:
        """Compact summary safe to send to the LLM (not the full dump)."""
        return {
            "page_width_pts": self.page_width_pts,
            "page_height_pts": self.page_height_pts,
            "title_block_text": self.title_block_text[:800],
            "text_span_count": len(self.text_spans),
            "line_segment_count": len(self.line_segments),
            "curve_count": len(self.curves),
            "color_summary": self.color_summary,
            "sample_text_labels": [
                s["text"] for s in self.text_spans[:30]
                if len(s["text"]) > 1
            ],
        }

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pdf_path": self.pdf_path,
            "page_index": self.page_index,
            "page_width_pts": self.page_width_pts,
            "page_height_pts": self.page_height_pts,
            "title_block_text": self.title_block_text,
            "text_spans": self.text_spans,
            "line_segments": self.line_segments,
            "curves": self.curves,
            "color_summary": self.color_summary,
            "full_text": self.full_text,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> ExtractionCache:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)


def _round_point(x: float, y: float) -> tuple[float, float]:
    return (round(x, 1), round(y, 1))


def parse_pdf_to_cache(
    pdf_path: str,
    page_index: int | None = None,
) -> ExtractionCache:
    """
    Parse all vectors and text from one PDF page into an ExtractionCache.

    If page_index is omitted, auto-selects the page with the most content
    (handles cover sheets and multi-page uploads).
    """
    doc = open_pdf_document(pdf_path)
    try:
        resolved_index = resolve_page_index(doc, page_index)
        page = load_page(doc, resolved_index)

        full_text = page.get_text("text") or ""
        title_block_text = full_text[:1200].strip()

        text_spans: list[dict] = []
        for block in page.get_text("dict")["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue
                    bbox = span["bbox"]
                    text_spans.append({
                        "text": text,
                        "x": round(bbox[0], 1),
                        "y": round(bbox[1], 1),
                        "x2": round(bbox[2], 1),
                        "y2": round(bbox[3], 1),
                        "cx": round((bbox[0] + bbox[2]) / 2, 1),
                        "cy": round((bbox[1] + bbox[3]) / 2, 1),
                        "font_size": round(span["size"], 1),
                    })

        line_segments: list[dict] = []
        color_summary: dict[str, int] = {}

        for path_item in page.get_drawings():
            color = path_item.get("color")
            color_key = str(list(color)) if color else "none"
            color_summary[color_key] = color_summary.get(color_key, 0) + 1
            width = path_item.get("width", 0) or 0

            for item in path_item["items"]:
                if item[0] != "l":
                    continue
                start = _round_point(item[1].x, item[1].y)
                end = _round_point(item[2].x, item[2].y)
                dx = end[0] - start[0]
                dy = end[1] - start[1]
                length_pts = math.sqrt(dx * dx + dy * dy)
                line_segments.append({
                    "start": list(start),
                    "end": list(end),
                    "length_pts": round(length_pts, 1),
                    "color_rgb": list(color) if color else None,
                    "stroke_width": round(width, 2),
                })

        curves: list[dict] = []
        for path_item in page.get_drawings():
            color = path_item.get("color")
            for item in path_item["items"]:
                if item[0] != "c":
                    continue
                pts = [p for p in item[1:5] if hasattr(p, "x")]
                if not pts:
                    continue
                curves.append({
                    "points": [[round(p.x, 1), round(p.y, 1)] for p in pts],
                    "color_rgb": list(color) if color else None,
                })

        return ExtractionCache(
            pdf_path=pdf_path,
            page_index=resolved_index,
            page_width_pts=round(page.rect.width, 1),
            page_height_pts=round(page.rect.height, 1),
            title_block_text=title_block_text,
            text_spans=text_spans,
            line_segments=line_segments,
            curves=curves,
            color_summary=color_summary,
            full_text=full_text,
        )
    finally:
        doc.close()
