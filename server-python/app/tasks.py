"""
Extraction as a Celery chord: one task per page, fanning in to a finalizer.

    extract_document ──► extract_page × N ──► finalize_extraction

Each page is appended to the job record the moment it finishes, so the
frontend can annotate page 1 while page 40 is still running. Pages retry
independently; a page that exhausts its retries is recorded as skipped so one
bad sheet never sinks the document.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import chord

from app.celery_app import celery_app
from app.config import settings
from app.extractor.cache import get_page_count
from app.extractor.llm import TokenTracker
from app.extractor.pipeline import NOT_A_PLAN_MESSAGE, process_page
from app.jobs import get_job, update_job
from app.models import DocumentProgress, ExtractionResult, JobRecord, utcnow
from app.notifier import notify_completion
from app.output_writer import save_extraction_output
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
    """Validate the upload, open the progress record and fan out one task per page."""
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
            job.result = ExtractionResult(document=DocumentProgress(page_count=page_count, pdf_path=str(document)))

        update_job(job_id, start)

        header = [extract_page.s(job_id, str(document), index) for index in range(page_count)]
        chord(header)(finalize_extraction.s(job_id))
    except Exception as exc:
        _fail(job_id, exc)
        raise


@celery_app.task(name="extract-page", bind=True, max_retries=MAX_RETRIES)
def extract_page(self, job_id: str, pdf_path: str, page_index: int) -> dict[str, Any]:
    """Annotate one page and publish it into the job record straight away."""
    try:
        page = process_page(pdf_path, page_index, TokenTracker())
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=RETRY_BACKOFF_S * 2**self.request.retries)
        log.warning("[%s] page %d failed after retries: %s", job_id, page_index + 1, exc)
        page = {
            "pageNumber": page_index + 1,
            "isPlan": False,
            "skippedReason": f"page could not be read: {exc}",
            "components": [],
        }

    def publish(job: JobRecord) -> None:
        if job.result is not None:
            job.result.add_page(page)

    update_job(job_id, publish)
    log.info("[%s] page %d: %s", job_id, page["pageNumber"], "plan" if page.get("isPlan") else "skipped")
    return page


@celery_app.task(name="finalize-extraction", ignore_result=True)
def finalize_extraction(pages: list[dict[str, Any]], job_id: str) -> None:
    """Close the result once every page is in: accepted flag, output file, webhook."""
    try:
        def complete(job: JobRecord) -> None:
            result = job.result
            if result is None:
                return
            for page in pages:  # defensive: a page is normally already published
                if page["pageNumber"] not in {p["pageNumber"] for p in result.pages}:
                    result.add_page(page)
            result.partial = False
            result.accepted = result.document.plan_page_count > 0
            result.extracted_at = utcnow()
            if not result.accepted:
                result.rejection = {"reason": "not_a_construction_plan", "message": NOT_A_PLAN_MESSAGE}
            job.status = "completed"
            job.completed_at = result.extracted_at

        job = update_job(job_id, complete)
        if job is None or job.result is None:
            raise UnrecoverableError("Job record disappeared before finalize")

        save_extraction_output(job_id, job.result)
        notify_completion(job)
    except Exception as exc:
        _fail(job_id, exc)
        raise
