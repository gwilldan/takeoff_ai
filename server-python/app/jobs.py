"""
Job records live in Redis as JSON under `<prefix><jobId>` with a sliding TTL.

`update_job` runs inside a WATCH transaction so page tasks finishing on
different workers at the same moment cannot overwrite each other's pages.
Takeoffs in flight are kept beside the job, keyed by QuantTakeoff's id.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from redis import WatchError

from app.config import settings
from app.models import JobRecord, utcnow
from app.redis_client import get_redis

Mutation = Callable[[JobRecord], None] | dict[str, Any]


def _key(job_id: str) -> str:
    return f"{settings.redis_job_prefix}{job_id}"


def save_job(job: JobRecord, pipe=None) -> JobRecord:
    (pipe or get_redis()).set(_key(job.id), job.model_dump_json(by_alias=True, exclude_none=True), ex=settings.job_ttl_seconds)
    return job


def create_job(job_id: str, document_path: str, notification_url: str | None) -> JobRecord:
    return save_job(JobRecord(id=job_id, document_path=document_path, notification_url=notification_url))


def get_job(job_id: str) -> JobRecord | None:
    raw = get_redis().get(_key(job_id))
    return JobRecord.model_validate_json(raw) if raw else None


def update_job(job_id: str, mutation: Mutation) -> JobRecord | None:
    """Apply a field patch or a mutator callable atomically; returns the saved record."""
    key = _key(job_id)

    with get_redis().pipeline() as pipe:
        while True:
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is None:
                    return None
                job = JobRecord.model_validate_json(raw)
                if callable(mutation):
                    mutation(job)
                else:
                    job = job.model_copy(update=mutation)
                job.updated_at = utcnow()
                pipe.multi()
                save_job(job, pipe)
                pipe.execute()
                return job
            except WatchError:
                continue


# A takeoff in flight: which job and page it belongs to, and what we read from
# the page before sending it. Lives until the page is published.
def _takeoff_key(takeoff_id: str) -> str:
    return f"{settings.redis_job_prefix}takeoff:{takeoff_id}"


def save_pending_takeoff(takeoff_id: str, pending: dict[str, Any]) -> None:
    get_redis().set(_takeoff_key(takeoff_id), json.dumps(pending), ex=settings.job_ttl_seconds)


def get_pending_takeoff(takeoff_id: str) -> dict[str, Any] | None:
    raw = get_redis().get(_takeoff_key(takeoff_id))
    return json.loads(raw) if raw else None


def drop_pending_takeoff(takeoff_id: str) -> None:
    get_redis().delete(_takeoff_key(takeoff_id))
