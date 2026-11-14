"""
Extraction package.

Imports here are deliberately lazy. The geometry modules (`grid`, `graph`,
`components`) are pure and must stay importable without the LLM stack — an
eager `from extractor.llm import ...` at package level would make every
geometry test require an OpenAI client and an API key.
"""

from pathlib import Path
from typing import Union


def extract_pdf_with_agent(pdf_path: Union[str, Path], **kwargs) -> dict:
    """Entry point used by main.py — delegates to the pipeline."""
    from extractor.pipeline import extract_pdf

    return extract_pdf(str(pdf_path), **kwargs)


__all__ = ["extract_pdf_with_agent"]
