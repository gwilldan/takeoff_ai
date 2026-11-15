"""
The job record the API serves and the worker updates.

Field names are snake_case in Python and camelCase on the wire, which is what
the frontend's `JobRecord` type expects. Always serialise with `to_wire()`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

JobStatus = Literal["queued", "processing", "completed", "failed"]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class DocumentProgress(WireModel):
    page_count: int
    pages_processed: int = 0
    plan_page_count: int = 0
    pdf_path: str | None = None


class ExtractionResult(WireModel):
    """Partial while pages are still arriving; final once `partial` is False."""

    partial: bool = True
    accepted: bool | None = None
    document: DocumentProgress
    pages: list[dict[str, Any]] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)
    rejection: dict[str, str] | None = None
    token_usage: dict[str, Any] | None = None
    extracted_at: str | None = None

    def add_page(self, page: dict[str, Any]) -> None:
        self.pages = sorted([*self.pages, page], key=lambda p: p["pageNumber"])
        self.document.pages_processed = len(self.pages)
        self.document.plan_page_count = sum(1 for p in self.pages if p.get("isPlan"))
        self.totals = page_totals(self.pages)
        self.token_usage = token_usage_totals(self.pages)


def token_usage_totals(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Every LLM call made for these pages, with summed totals; updated as pages land."""
    calls = [c for p in pages for c in (p.get("tokenUsage") or {}).get("calls", [])]
    return {
        "calls": calls,
        "totals": {k: sum(c.get(k, 0) for c in calls) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
    }


def page_totals(pages: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for page in pages:
        if page.get("isPlan"):
            for layer, count in (page.get("counts") or {}).items():
                totals[layer] = totals.get(layer, 0) + count
    return totals


class JobRecord(WireModel):
    id: str
    status: JobStatus = "queued"
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    document_path: str
    notification_url: str | None = None
    attempts: int = 0
    result: ExtractionResult | None = None
    error: str | None = None
    completed_at: str | None = None
