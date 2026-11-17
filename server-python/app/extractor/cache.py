"""
Parse one PDF page into an in-memory cache: text spans, vector lines, images.

Coordinates are PDF points with a top-left origin — the same space pdf.js
reports at scale 1, so anything stored here drives the frontend overlay with
no transform.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from app.config import settings
from app.extractor.pdf_utils import load_page, open_pdf_document

log = logging.getLogger(__name__)

# A page with fewer native text spans than this, and a drawing-sized image,
# is a scan: its text has to be read with OCR.
MIN_NATIVE_SPANS = 20
MIN_SCAN_IMAGE_COVERAGE = 0.2


@dataclass
class ExtractionCache:
    pdf_path: str
    page_index: int
    page_width_pts: float
    page_height_pts: float
    full_text: str = ""
    text_spans: list[dict] = field(default_factory=list)
    line_segments: list[dict] = field(default_factory=list)
    # Closed vector regions a CAD export uses for cut elements: hatch clip paths
    # and dark-filled paths, as point lists. Wall evidence on vector sheets.
    cut_regions: list[list[list[float]]] = field(default_factory=list)
    image_coverage: float = 0.0  # share of the page covered by embedded images
    text_source: str = "native"  # or "ocr" when the page had no usable text layer

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


def _collect_text_spans(page, textpage=None) -> list[dict]:
    spans = []
    for block in page.get_text("dict", textpage=textpage)["blocks"]:
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


def _path_points(items) -> list[list[float]]:
    points = []
    for item in items:
        if item[0] == "l":
            points += [item[1], item[2]]
        elif item[0] == "c":
            points += [item[1], item[4]]
        elif item[0] == "re":
            points += [item[1].tl, item[1].tr, item[1].br, item[1].bl]
        elif item[0] == "qu":
            points += [item[1].ul, item[1].ur, item[1].lr, item[1].ll]
    return [[round(p.x, 2), round(p.y, 2)] for p in points]


def _collect_vectors(page) -> tuple[list[dict], list[list[list[float]]]]:
    """
    Stroked lines and cut regions in one pass (get_drawings re-walks the
    content stream each call).

    A cut region is a clip path that hatching is drawn inside — how ArchiCAD
    and Revit draw a cut wall — or a path filled darker than light grey, how
    AutoCAD draws a solid wall. Clip paths larger than a twentieth of the page
    are viewports, not elements.
    """
    lines, regions = [], []
    page_area = page.rect.width * page.rect.height
    paths = page.get_drawings(extended=True)

    for index, path in enumerate(paths):
        if path["type"] == "clip":
            following = paths[index + 1] if index + 1 < len(paths) else None
            clips_drawing = following and following["type"] != "clip" and following.get("level", 0) > path.get("level", 0)
            if clips_drawing and path["scissor"].width * path["scissor"].height < page_area / 20:
                regions.append(_path_points(path["items"]))
            continue
        if path.get("fill") and max(path["fill"]) < 0.8:
            regions.append(_path_points(path["items"]))
        if "s" not in path["type"]:
            continue
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
    return lines, [r for r in regions if len(r) >= 3]


def _image_coverage(page) -> float:
    page_area = page.rect.width * page.rect.height
    covered = sum(
        max(0.0, (page.rect & info["bbox"]).width * (page.rect & info["bbox"]).height)
        for info in page.get_image_info()
    )
    return min(1.0, covered / page_area) if page_area else 0.0


def _ocr_textpage(page):
    """Tesseract text layer for a scanned page, or None when OCR is unavailable."""
    try:
        return page.get_textpage_ocr(flags=0, language=settings.ocr_language, dpi=settings.ocr_dpi, full=True)
    except Exception as exc:  # no tesseract binary / tessdata in this environment
        log.warning("OCR unavailable, scanned page read without text: %s", exc)
        return None


def parse_pdf_to_cache(pdf_path: str, page_index: int) -> ExtractionCache:
    doc = open_pdf_document(pdf_path)
    try:
        page = load_page(doc, page_index)
        coverage = _image_coverage(page)
        spans, full_text, source = _collect_text_spans(page), page.get_text("text") or "", "native"

        if len(spans) < MIN_NATIVE_SPANS and coverage >= MIN_SCAN_IMAGE_COVERAGE and (textpage := _ocr_textpage(page)):
            spans, full_text, source = _collect_text_spans(page, textpage), page.get_text("text", textpage=textpage), "ocr"

        lines, regions = _collect_vectors(page)

        return ExtractionCache(
            pdf_path=pdf_path,
            page_index=page_index,
            page_width_pts=round(page.rect.width, 1),
            page_height_pts=round(page.rect.height, 1),
            full_text=full_text,
            text_spans=spans,
            line_segments=lines,
            cut_regions=regions,
            image_coverage=coverage,
            text_source=source,
        )
    finally:
        doc.close()
