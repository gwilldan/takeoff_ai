"""
Safe PDF open/load helpers with clear errors for common failure modes.

PyMuPDF returns page=None (instead of raising) when the page index is out of
range — calling .get_text() on that then fails with "page is None".
"""

from __future__ import annotations

from pathlib import Path

import fitz


class PdfPageError(ValueError):
    """Raised when a PDF page cannot be loaded."""


def open_pdf_document(pdf_path: str) -> fitz.Document:
    path = Path(pdf_path)
    if not path.is_file():
        raise PdfPageError(f"PDF file not found: {pdf_path}")

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise PdfPageError(f"Cannot open PDF '{pdf_path}': {exc}") from exc

    if doc.is_encrypted and doc.needs_pass:
        doc.close()
        raise PdfPageError(f"PDF is password-protected: {pdf_path}")

    if doc.page_count == 0:
        doc.close()
        raise PdfPageError(f"PDF has no pages: {pdf_path}")

    return doc


def load_page(doc: fitz.Document, page_index: int) -> fitz.Page:
    if page_index < 0 or page_index >= doc.page_count:
        raise PdfPageError(
            f"Page index {page_index} is out of range — "
            f"document has {doc.page_count} page(s)"
        )

    page = doc[page_index]
    if page is None:
        raise PdfPageError(
            f"Page {page_index} could not be loaded (returned None) — "
            f"the PDF may be corrupt. page_count={doc.page_count}"
        )

    return page


def _page_content_score(page: fitz.Page) -> int:
    text_len = len(page.get_text("text") or "")
    try:
        drawing_count = len(page.get_drawings())
    except Exception:
        drawing_count = 0
    return text_len + drawing_count * 10


def find_best_page_index(doc: fitz.Document) -> int:
    """
    Pick the page most likely to contain the floor plan — most text + vectors.
    Handles multi-page PDFs where page 0 is a cover sheet.
    """
    best_index = 0
    best_score = -1

    for i in range(doc.page_count):
        page = doc[i]
        if page is None:
            continue
        score = _page_content_score(page)
        if score > best_score:
            best_score = score
            best_index = i

    if doc[best_index] is None:
        raise PdfPageError(
            f"No readable pages found in PDF (page_count={doc.page_count})"
        )

    return best_index


def resolve_page_index(doc: fitz.Document, page_index: int | None = None) -> int:
    """
    Resolve a valid page index.

    When page_index is None, auto-selects the richest content page.
    When page_index is given but that page is empty, falls back to best page.
    """
    if page_index is None:
        return find_best_page_index(doc)

    if page_index < 0 or page_index >= doc.page_count:
        raise PdfPageError(
            f"Page index {page_index} is out of range — "
            f"document has {doc.page_count} page(s)"
        )

    page = load_page(doc, page_index)
    if _page_content_score(page) == 0:
        best = find_best_page_index(doc)
        if best != page_index:
            print(
                f"[pdf] Page {page_index} is empty — "
                f"falling back to page {best}"
            )
            return best

    return page_index
