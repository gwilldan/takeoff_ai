"""
Render PDF pages and crop tiles for vision-based profiling.
"""

from __future__ import annotations

import base64

import fitz

from extractor.pdf_utils import load_page, open_pdf_document


def render_page_png(pdf_path: str, page_index: int = 0, dpi: int = 150) -> bytes:
    doc = open_pdf_document(pdf_path)
    try:
        page = load_page(doc, page_index)
        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        return pixmap.tobytes("png")
    finally:
        doc.close()


def render_page_base64(pdf_path: str, page_index: int = 0, dpi: int = 150) -> str:
    return base64.b64encode(render_page_png(pdf_path, page_index, dpi)).decode("ascii")


def render_crop_base64(
    pdf_path: str,
    center_pts: tuple[float, float],
    crop_size_pts: float = 300,
    page_index: int = 0,
    dpi: int = 150,
) -> str:
    """Render a square crop centred on a PDF point, returned as base64 PNG."""
    doc = open_pdf_document(pdf_path)
    try:
        page = load_page(doc, page_index)
        cx, cy = center_pts
        half = crop_size_pts / 2
        page_rect = page.rect
        clip = fitz.Rect(
            max(page_rect.x0, cx - half),
            max(page_rect.y0, cy - half),
            min(page_rect.x1, cx + half),
            min(page_rect.y1, cy + half),
        )
        if clip.is_empty or clip.width < 1 or clip.height < 1:
            raise ValueError(
                f"Crop region is outside page bounds: center=({cx}, {cy}), "
                f"page_size=({page_rect.width}, {page_rect.height})"
            )
        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        return base64.b64encode(pixmap.tobytes("png")).decode("ascii")
    finally:
        doc.close()
