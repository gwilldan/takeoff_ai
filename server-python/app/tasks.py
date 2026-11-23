"""
Extraction tasks: read every page ourselves, measure the floor plans with
QuantTakeoff, publish each page as soon as it is done.

    extract_document ──► extract_page × N ──(floor plan, as a one-page PDF)──► QuantTakeoff
                                                                                 │
                              callback or poll ──► collect_takeoff ◄─────────────┘ ──► page published

There is no chord: a takeoff finishes minutes after the task that started it,
so whichever page is published last closes the job. QuantTakeoff's callback
only makes collection happen sooner; polling alone always gets there.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx
from celery.exceptions import Retry

from app.celery_app import celery_app
from app.config import settings
from app import takeoff_api
from app.extractor.cache import get_page_count
from app.extractor.llm import TokenTracker
from app.extractor.pipeline import NOT_A_PLAN_MESSAGE, complete_page, read_page
from app.jobs import drop_pending_takeoff, get_job, get_pending_takeoff, save_pending_takeoff, update_job
from app.models import DocumentProgress, JobRecord, TakeoffResult, utcnow
from app.notifier import notify_completion
from app.output_writer import save_extraction_output
from app.training import save_draft
from app.storage import resolve_document

log = logging.getLogger(__name__)

RETRY_BACKOFF_S = max(settings.queue_backoff_delay_ms / 1000, 1)
MAX_RETRIES = settings.queue_attempts - 1


class UnrecoverableError(Exception):
    """Retrying would not help; the job fails on the first attempt."""


def enqueue_extraction(job: JobRecord) -> None:
    extract_document.apply_async(kwargs={"job_id": job.id}, task_id=job.id, queue=settings.queue_name)


def _fail(job_id: str, error: Exception) -> None:
    update_job(job_id, {"status": "failed", "error": str(error), "completed_at": utcnow()})


def _webhook_url() -> str | None:
    return f"{settings.public_api_url.rstrip('/')}/webhooks/quanttakeoff" if settings.public_api_url else None


@celery_app.task(
    name="extract-document",
    ignore_result=True,
    autoretry_for=(Exception,),
    dont_autoretry_for=(UnrecoverableError,),
    max_retries=MAX_RETRIES,
    retry_backoff=RETRY_BACKOFF_S,
    retry_jitter=False,
)
def extract_document(job_id: str) -> None:
    """Open the result and fan out one task per page."""
    job = get_job(job_id)
    if job is None:
        raise UnrecoverableError("Job record not found")

    try:
        try:
            document = resolve_document(job.document_path)
        except ValueError as exc:
            raise UnrecoverableError(str(exc)) from exc

        page_count = get_page_count(str(document))
        log.info("[%s] %s — %d page(s)", job_id, document, page_count)

        def start(job: JobRecord) -> None:
            job.status = "processing"
            job.attempts += 1
            job.error = None
            job.result = TakeoffResult(id=job_id, document=DocumentProgress(page_count=page_count),
                                       timings={"started_at": utcnow()})

        update_job(job_id, start)
        for index in range(page_count):
            extract_page.delay(job_id, str(document), index)
    except Exception as exc:
        _fail(job_id, exc)
        raise


@celery_app.task(name="extract-page", bind=True, ignore_result=True, max_retries=MAX_RETRIES)
def extract_page(self, job_id: str, pdf_path: str, page_index: int) -> None:
    """Classify a page; publish it if it is not a floor plan, else start its takeoff."""
    try:
        page = read_page(pdf_path, page_index, TokenTracker())
        if page["is_plan"]:
            takeoff_id = takeoff_api.create_takeoff(
                pdf_path, page_index, webhook_url=_webhook_url(), idempotency_key=f"{job_id}:{page_index}",
            )
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=RETRY_BACKOFF_S * 2**self.request.retries)
        log.warning("[%s] page %d failed after retries: %s", job_id, page_index + 1, exc)
        if "page" in locals():  # read, but the takeoff could not be started
            _publish(job_id, {**page, "error": f"takeoff could not be started: {exc}"})
        else:
            _publish(job_id, {"page_index": page_index, "is_plan": False, "detections": [],
                              "skipped_reason": f"page could not be read: {exc}"})
        return

    if not page["is_plan"]:
        _publish(job_id, page)
        return
    save_pending_takeoff(takeoff_id, {"job_id": job_id, "page": page, "submitted_at": time.time()})
    collect_takeoff.apply_async(args=[takeoff_id], countdown=settings.takeoff_poll_interval_s)
    log.info("[%s] page %d: floor plan, takeoff %s", job_id, page_index + 1, takeoff_id)


@celery_app.task(name="collect-takeoff", bind=True, ignore_result=True, max_retries=None)
def collect_takeoff(self, takeoff_id: str) -> None:
    """Fetch a finished takeoff and publish its page; check again later while it runs."""
    pending = get_pending_takeoff(takeoff_id)
    if pending is None:
        return  # already published, by the callback or an earlier poll
    page, waited = pending["page"], time.time() - pending["submitted_at"]

    try:
        takeoff = takeoff_api.get_takeoff(takeoff_id)
        status = takeoff["status"]
        if status not in takeoff_api.TERMINAL:
            if waited > settings.takeoff_timeout_s:
                raise takeoff_api.TakeoffApiError(f"takeoff still {status} after {waited / 60:.0f} minutes")
            raise self.retry(countdown=settings.takeoff_poll_interval_s)
        if status != "succeeded":
            raise takeoff_api.TakeoffApiError(f"takeoff {status}: {takeoff.get('error') or 'no reason given'}")

        result = takeoff_api.get_results(takeoff_id)
        if not result.get("pages"):
            raise takeoff_api.TakeoffApiError(result.get("error") or "takeoff returned no page")
        page = complete_page(page, result["pages"][0], result.get("metrics"))
        page["takeoff_id"] = takeoff_id
        page["timings"]["takeoff_s"] = round(waited, 1)
        _publish(pending["job_id"], page, schema_version=result.get("schema_version"),
                 wall_types=result.get("wall_types") or [], review=result.get("review"), assets=result.get("assets") or [])
    except Retry:
        raise
    except (takeoff_api.TakeoffApiError, httpx.HTTPError) as exc:
        if getattr(exc, "transient", True) and waited < settings.takeoff_timeout_s:
            raise self.retry(countdown=settings.takeoff_poll_interval_s)
        log.warning("[%s] takeoff %s: %s", pending["job_id"], takeoff_id, exc)
        _publish(pending["job_id"], {**page, "takeoff_id": takeoff_id, "error": str(exc)})
    drop_pending_takeoff(takeoff_id)


def _publish(job_id: str, page: dict[str, Any], **takeoff: Any) -> None:
    """Add a page to the result; the page that completes the document also closes the job."""
    closed = False

    def publish(job: JobRecord) -> None:
        nonlocal closed
        result = job.result
        closed = False
        if result is None or result.has_page(page["page_index"]):
            return
        result.add_page(page, **takeoff)
        if result.complete:
            result.status = "succeeded"
            result.accepted = result.document.plan_page_count > 0
            if not result.accepted:
                result.rejection = {"reason": "no_floor_plan", "message": NOT_A_PLAN_MESSAGE}
            job.status = "completed"
            job.completed_at = utcnow()
            result.timings["completed_at"] = job.completed_at
            started = datetime.fromisoformat(result.timings["started_at"])
            result.timings["elapsed_s"] = round((datetime.fromisoformat(job.completed_at) - started).total_seconds(), 1)
            closed = True

    job = update_job(job_id, publish)
    log.info("[%s] page %d published", job_id, page["page_index"] + 1)
    if closed and job is not None and job.result is not None:
        save_extraction_output(job_id, job.result)
        try:
            save_draft(job)
        except OSError as exc:  # keeping a training copy must never fail the job
            log.warning("[%s] training draft not saved: %s", job_id, exc)
        notify_completion(job)

