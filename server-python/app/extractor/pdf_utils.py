"""Safe PDF open/load helpers with clear errors for the common failure modes."""

from __future__ import annotations

from pathlib import Path

import fitz


class PdfPageError(ValueError):
    """Raised when a PDF or one of its pages cannot be loaded."""


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
    """PyMuPDF returns None for an out-of-range page instead of raising; we raise."""
    if not 0 <= page_index < doc.page_count:
        raise PdfPageError(f"Page index {page_index} is out of range — document has {doc.page_count} page(s)")

    page = doc[page_index]
    if page is None:
        raise PdfPageError(f"Page {page_index} could not be loaded — the PDF may be corrupt")
    return page
