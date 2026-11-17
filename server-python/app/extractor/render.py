"""Render PDF pages for the vision model and for pixel-space room detection."""

from __future__ import annotations

import base64

import fitz
import numpy as np

from app.extractor.pdf_utils import load_page, open_pdf_document


def _pixmap(pdf_path: str, page_index: int, zoom: float, antialias: bool = True) -> fitz.Pixmap:
    doc = open_pdf_document(pdf_path)
    previous = fitz.TOOLS.show_aa_level()
    try:
        if not antialias:
            fitz.TOOLS.set_aa_level(0)
        return load_page(doc, page_index).get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    finally:
        fitz.TOOLS.set_aa_level(previous["graphics"])
        doc.close()


def render_page_base64(pdf_path: str, page_index: int, dpi: int = 150) -> str:
    return base64.b64encode(_pixmap(pdf_path, page_index, dpi / 72).tobytes("png")).decode("ascii")


def render_page_array(pdf_path: str, page_index: int, px_per_pt: float, antialias: bool = True) -> np.ndarray:
    """RGB uint8 array, `px_per_pt` pixels per PDF point."""
    pix = _pixmap(pdf_path, page_index, px_per_pt, antialias)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
