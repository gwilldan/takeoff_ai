from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import fitz
import pdfplumber


@dataclass
class ExtractedTable:
    rows: List[List[str]]


@dataclass
class ExtractedPage:
    pageNumber: int
    text: str
    tables: List[ExtractedTable]


@dataclass
class PdfExtractionResult:
    pageCount: int
    text: str
    pages: List[ExtractedPage]
    extractedAt: str


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
