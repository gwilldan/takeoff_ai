"""
The job record the API serves and the worker updates.

The job wrapper is camelCase on the wire, which is what the frontend's
`JobRecord` type expects; always serialise with `to_wire()`. The result inside
it is QuantTakeoff's takeoff result format (snake_case, their field names and
meanings) with our fields added beside theirs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

JobStatus = Literal["queued", "processing", "completed", "failed"]

ROOM_CLASSES = {"space", "room"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class DocumentProgress(BaseModel):
    page_count: int
    pages_processed: int = 0
    plan_page_count: int = 0


class TakeoffResult(BaseModel):
    """
    QuantTakeoff's result for the whole document, filled one page at a time.

    Theirs: id, status, schema_version, pages, metrics, assets, error,
    wall_types, review. Ours: document, accepted, rejection, token_usage,
    timings. `metrics` is ours over the whole document; each page keeps its own
    takeoff's metrics as QuantTakeoff wrote them. Pages that are not floor plans carry `is_plan: false` and a
    `skipped_reason` and were never sent to QuantTakeoff.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    status: Literal["queued", "running", "succeeded", "failed"] = "running"
    schema_version: str | None = None
    pages: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    assets: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    wall_types: list[dict[str, Any]] = Field(default_factory=list)
    review: dict[str, int] = Field(default_factory=lambda: {"doors": 0, "windows": 0})

    document: DocumentProgress
    accepted: bool | None = None
    rejection: dict[str, str] | None = None
    token_usage: dict[str, Any] | None = None
    timings: dict[str, Any] = Field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.document.pages_processed >= self.document.page_count

    def has_page(self, page_index: int) -> bool:
        return any(p["page_index"] == page_index for p in self.pages)

    def add_page(self, page: dict[str, Any], *, schema_version: str | None = None, wall_types=(), review=None,
                 assets=()) -> None:
        """Publish a page and roll its numbers into the document's. A page already in is ignored."""
        if self.has_page(page["page_index"]):
            return
        self.pages = sorted([*self.pages, page], key=lambda p: p["page_index"])
        self.schema_version = self.schema_version or schema_version
        self.wall_types += [{**w, "page_index": page["page_index"]} for w in wall_types]
        self.assets += list(assets)
        for key, count in (review or {}).items():
            self.review[key] = self.review.get(key, 0) + count
        self.document.pages_processed = len(self.pages)
        self.document.plan_page_count = sum(1 for p in self.pages if p.get("is_plan"))
        self.metrics = document_metrics(self.pages)
        calls = [c for p in self.pages for c in (p.get("token_usage") or {}).get("calls", [])]
        self.token_usage = {
            "calls": calls,
            "totals": {k: sum(c.get(k, 0) for c in calls) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
        }

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def document_metrics(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts, wall length and room area over every page, and how long each stage took."""
    detections = [d for p in pages for d in p.get("detections") or []]
    counts: dict[str, int] = {}
    for d in detections:
        counts[d["class_name"]] = counts.get(d["class_name"], 0) + 1
    takeoffs = [t["takeoff_s"] for p in pages if (t := p.get("timings") or {}).get("takeoff_s") is not None]
    reads = [t["read_s"] for p in pages if (t := p.get("timings") or {}).get("read_s") is not None]
    return {
        "counts": counts,
        "wall_length_m": round(sum(d.get("length_m") or 0 for d in detections if d["class_name"] == "wall"), 2),
        # At our scale where it replaced QuantTakeoff's estimate.
        "room_area_m2": round(sum(d.get("area_m2_checked", d.get("area_m2")) or 0
                                  for d in detections if d["class_name"] in ROOM_CLASSES), 2),
        "seconds": {
            "read_mean": round(sum(reads) / len(reads), 1) if reads else None,
            "takeoff_mean": round(sum(takeoffs) / len(takeoffs), 1) if takeoffs else None,
            "takeoff_max": max(takeoffs, default=None),
        },
    }


class JobRecord(WireModel):
    id: str
    status: JobStatus = "queued"
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    document_path: str
    notification_url: str | None = None
    attempts: int = 0
    result: TakeoffResult | None = None
    error: str | None = None
    completed_at: str | None = None
