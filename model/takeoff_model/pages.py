"""
Pages in: PDFs or images, rendered at a resolution set by the drawing scale,
and the text printed on them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
SOURCE_SUFFIXES = {".pdf", *IMAGE_SUFFIXES}
SCALE_TEXT = re.compile(r"\b1\s*:\s*(\d{1,4})\b")


@dataclass
class Span:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def page_count(path: str | Path) -> int:
    if is_image(path):
        return 1
    with pymupdf.open(path) as doc:
        return len(doc)


def render_dpi(denominator: float | None, render: dict) -> float:
    """DPI at which one pixel covers `render.mm_per_px` real millimetres, or the fallback."""
    if not denominator:
        return render["fallback_dpi"]
    dpi = denominator * 25.4 / render["mm_per_px"]
    return float(min(max(dpi, render["min_dpi"]), render["max_dpi"]))


def mm_per_px(denominator: float | None, dpi: float) -> float | None:
    return denominator * 25.4 / dpi if denominator else None


def stated_scale(path: str | Path, page_index: int) -> float | None:
    """The scale printed on a PDF page ("1:100"), if any. Images carry no text."""
    if is_image(path):
        return None
    with pymupdf.open(path) as doc:
        found = [int(m.group(1)) for m in SCALE_TEXT.finditer(doc[page_index].get_text())]
    found = [d for d in found if 5 <= d <= 2500]
    return float(max(set(found), key=found.count)) if found else None


def render(path: str | Path, page_index: int, dpi: float, render_cfg: dict) -> np.ndarray:
    """The page as RGB uint8 at `dpi`."""
    if is_image(path):
        image = cv2.cvtColor(cv2.imread(str(path), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        factor = dpi / render_cfg["image_dpi"]
        if abs(factor - 1) > 1e-3:
            size = (round(image.shape[1] * factor), round(image.shape[0] * factor))
            image = cv2.resize(image, size, interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)
        return image
    with pymupdf.open(path) as doc:
        pix = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), colorspace=pymupdf.csRGB, alpha=False)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()


def text_spans(path: str | Path, page_index: int, px_per_pt: float, ocr: bool = True) -> list[Span]:
    """
    Text on the page in rendered pixels. A page with almost no text layer is a
    scan; it is read with Tesseract when installed.
    """
    if is_image(path):
        return []
    with pymupdf.open(path) as doc:
        page = doc[page_index]
        textpage = None
        if ocr and len(page.get_text("words")) < 20:
            try:
                textpage = page.get_textpage_ocr(full=True, dpi=300)
            except Exception:
                textpage = None  # no Tesseract: names come only from a text layer
        spans = []
        for block in page.get_text("dict", textpage=textpage)["blocks"]:
            for line in block.get("lines", []):
                for s in line["spans"]:
                    if text := s["text"].strip():
                        x0, y0, x1, y1 = (v * px_per_pt for v in s["bbox"])
                        spans.append(Span(text, x0, y0, x1, y1, s["size"]))
        return spans
