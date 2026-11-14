"""
Job records live in Redis as JSON under `<prefix><jobId>` with a sliding TTL.

Both the API and the worker read and write through these three functions, so
the record shape the frontend polls is defined in exactly one place.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from app.config import settings
from app.redis_client import get_redis

JobStatus = Literal["queued", "processing", "completed", "failed"]
Job = dict[str, Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(job_id: str) -> str:
    return f"{settings.redis_job_prefix}{job_id}"


def create_job(job_id: str, document_path: str, notification_url: str | None) -> Job:
    now = _now()
    job: Job = {
        "id": job_id,
        "status": "queued",
        "createdAt": now,
        "updatedAt": now,
        "documentPath": document_path,
        "attempts": 0,
    }
    if notification_url:
        job["notificationUrl"] = notification_url
    save_job(job)
    return job


def save_job(job: Job) -> None:
    get_redis().set(_key(job["id"]), json.dumps(job, default=str), ex=settings.job_ttl_seconds)


def get_job(job_id: str) -> Job | None:
    raw = get_redis().get(_key(job_id))
    return json.loads(raw) if raw else None


def update_job(job_id: str, patch: Job) -> Job | None:
    job = get_job(job_id)
    if job is None:
        return None
    job.update(patch, updatedAt=_now())
    save_job(job)
    return job
