from pathlib import Path
from typing import Union

from extractor.pipeline import extract_pdf
from extractor.llm import TokenTracker, LLMUsage


def extract_pdf_with_agent(pdf_path: Union[str, Path]) -> dict:
    """Legacy entry point used by main.py — delegates to extract_pdf()."""
    return extract_pdf(str(pdf_path))


__all__ = ["extract_pdf", "extract_pdf_with_agent", "TokenTracker", "LLMUsage"]
