"""
The extraction task — one Celery task per uploaded document.

Mirrors the job lifecycle the frontend polls: queued → processing (with partial
page results streamed into `result`) → completed | failed. Transient failures
retry with exponential backoff; unrecoverable ones (bad path, missing record)
fail immediately.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.celery_app import celery_app
from app.config import settings
from app.extractor import extract_pdf_with_agent
from app.extractor.cache import get_page_count
from app.jobs import get_job, update_job
from app.notifier import build_notification_payload, send_notification
from app.output_writer import save_extraction_output
from app.storage import resolve_document

log = logging.getLogger(__name__)


class UnrecoverableError(Exception):
    """Retrying would not help; the job fails on the first attempt."""


def enqueue_extraction(job: dict[str, Any]) -> None:
    extract_document.apply_async(
        kwargs={"job_id": job["id"]},
        task_id=job["id"],
        queue=settings.queue_name,
    )


def _page_writer(job_id: str, page_count: int):
    """Publish each finished page into the job's `result` so the poll shows progress."""
    pages: list[dict[str, Any]] = []

    def on_page(page: dict[str, Any]) -> None:
        pages.append(page)
        update_job(job_id, {
            "status": "processing",
            "result": {
                "partial": True,
                "document": {
                    "pageCount": page_count,
                    "pagesProcessed": len(pages),
                    "planPageCount": sum(1 for p in pages if p.get("isPlan")),
                },
                "pages": list(pages),
            },
        })

    return on_page


@celery_app.task(
    name="extract-document",
    bind=True,
    autoretry_for=(Exception,),
    dont_autoretry_for=(UnrecoverableError,),
    max_retries=settings.queue_attempts - 1,
    retry_backoff=max(settings.queue_backoff_delay_ms / 1000, 1),
    retry_jitter=False,
)
def extract_document(self, job_id: str) -> None:
    job = get_job(job_id)
    if job is None:
        raise UnrecoverableError("Job record not found")

    update_job(job_id, {"status": "processing", "attempts": int(job.get("attempts", 0)) + 1})

    try:
        try:
            document = resolve_document(job.get("documentPath") or "")
        except ValueError as exc:
            raise UnrecoverableError(str(exc)) from exc

        page_count = get_page_count(str(document))
        result = extract_pdf_with_agent(document, on_page=_page_writer(job_id, page_count))
        save_extraction_output(job_id, result)

        completed = update_job(job_id, {
            "status": "completed",
            "result": result,
            "error": None,
            "completedAt": result.get("extractedAt"),
        }) or job

        _notify(completed, result)
    except Exception as exc:
        update_job(job_id, {
            "status": "failed",
            "error": str(exc),
            "completedAt": datetime.now(timezone.utc).isoformat(),
        })
        raise


def _notify(job: dict[str, Any], result: dict[str, Any]) -> None:
    url = job.get("notificationUrl") or settings.notification_webhook_url
    if not url:
        return
    try:
        send_notification(url, build_notification_payload(job, result))
    except Exception as exc:
        log.warning("Notification failed for job %s: %s", job["id"], exc)
