"""
Parse one PDF page into an in-memory cache: text spans, vector lines, images.

Coordinates are PDF points with a top-left origin — the same space pdf.js
reports at scale 1, so anything stored here drives the frontend overlay with
no transform.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.extractor.pdf_utils import load_page, open_pdf_document


@dataclass
class ExtractionCache:
    pdf_path: str
    page_index: int
    page_width_pts: float
    page_height_pts: float
    full_text: str = ""
    text_spans: list[dict] = field(default_factory=list)
    line_segments: list[dict] = field(default_factory=list)
    image_coverage: float = 0.0  # share of the page covered by embedded images

    @property
    def page_number(self) -> int:
        return self.page_index + 1

    @property
    def title_block_text(self) -> str:
        return self.full_text[:1200].strip()

    def to_summary(self) -> dict:
        """Compact description safe to put in front of the LLM."""
        return {
            "page_size_pts": [self.page_width_pts, self.page_height_pts],
            "title_block_text": self.title_block_text[:800],
            "text_span_count": len(self.text_spans),
            "line_segment_count": len(self.line_segments),
            "image_coverage": round(self.image_coverage, 2),
            "sample_text_labels": [s["text"] for s in self.text_spans[:30] if len(s["text"]) > 1],
        }


def get_page_count(pdf_path: str) -> int:
    doc = open_pdf_document(pdf_path)
    try:
        return doc.page_count
    finally:
        doc.close()


def _collect_text_spans(page) -> list[dict]:
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                x0, y0, x1, y1 = (round(v, 1) for v in span["bbox"])
                spans.append({
                    "text": text, "x": x0, "y": y0, "x2": x1, "y2": y1,
                    "cx": round((x0 + x1) / 2, 1), "cy": round((y0 + y1) / 2, 1),
                    "font_size": round(span["size"], 1),
                })
    return spans


def _collect_lines(page) -> list[dict]:
    """One pass over the drawings: get_drawings() re-walks the content stream each call."""
    lines = []
    for path in page.get_drawings():
        color = list(path["color"]) if path.get("color") else None
        width = round(path.get("width") or 0, 2)
        for item in path["items"]:
            if item[0] != "l":
                continue
            start, end = item[1], item[2]
            lines.append({
                "start": [round(start.x, 1), round(start.y, 1)],
                "end": [round(end.x, 1), round(end.y, 1)],
                "length_pts": round(math.dist(start, end), 1),
                "color_rgb": color,
                "stroke_width": width,
            })
    return lines


def _image_coverage(page) -> float:
    page_area = page.rect.width * page.rect.height
    covered = sum(
        max(0.0, (page.rect & info["bbox"]).width * (page.rect & info["bbox"]).height)
        for info in page.get_image_info()
    )
    return min(1.0, covered / page_area) if page_area else 0.0


def parse_pdf_to_cache(pdf_path: str, page_index: int) -> ExtractionCache:
    doc = open_pdf_document(pdf_path)
    try:
        page = load_page(doc, page_index)
        return ExtractionCache(
            pdf_path=pdf_path,
            page_index=page_index,
            page_width_pts=round(page.rect.width, 1),
            page_height_pts=round(page.rect.height, 1),
            full_text=page.get_text("text") or "",
            text_spans=_collect_text_spans(page),
            line_segments=_collect_lines(page),
            image_coverage=_image_coverage(page),
        )
    finally:
        doc.close()
