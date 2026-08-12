from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from bullmq import UnrecoverableError, Worker
from redis import Redis

from config import Settings, load_settings
from extractor import extract_pdf_with_agent
from job_store import load_job, update_job
from notifier import send_notification


def redis_client(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def build_notification_payload(
    job: Dict[str, Any],
    status: str,
    result: Dict[str, Any] | None = None,
    error: str | None = None
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "jobId": job["id"],
        "status": status,
        "source": {
            "documentPath": job.get("documentPath")
        },
        "completedAt": job.get("completedAt")
    }

    if result is not None:
        payload["result"] = result

    if error is not None:
        payload["error"] = error

    return payload


def resolve_document_path(settings: Settings, job: Dict[str, Any]) -> Path:
    raw_path = job.get("documentPath")
    if not raw_path:
        raise UnrecoverableError("Job is missing documentPath")

    document_path = Path(raw_path).expanduser().resolve()
    upload_root = Path(settings.document_storage_dir).expanduser().resolve()

    try:
        document_path.relative_to(upload_root)
    except ValueError as exc:
        raise UnrecoverableError("Document path is outside the upload volume") from exc

    if not document_path.is_file():
        raise UnrecoverableError("Uploaded document is missing")

    return document_path


def to_result_payload(extraction: Any) -> Dict[str, Any]:
    return {
        "pageCount": extraction.pageCount,
        "text": extraction.text,
        "pages": [
            {
                "pageNumber": page.pageNumber,
                "text": page.text,
                "tables": [
                    {
                        "rows": table.rows
                    }
                    for table in page.tables
                ]
            }
            for page in extraction.pages
        ],
        "extractedAt": extraction.extractedAt
    }


async def process_job(job: Any, _token: str, settings: Settings, redis: Redis) -> Dict[str, Any]:
    job_id = job.data.get("jobId") or job.id
    job_record = await asyncio.to_thread(load_job, redis, settings.redis_job_prefix, job_id)

    if job_record is None:
        raise UnrecoverableError("Job record not found")

    await asyncio.to_thread(
        update_job,
        redis,
        settings.redis_job_prefix,
        job_id,
        {
            "status": "processing",
            "attempts": int(job_record.get("attempts", 0)) + 1
        },
        settings.job_ttl_seconds
    )

    try:
        document_path = resolve_document_path(settings, job.data)
        extraction = await asyncio.to_thread(extract_pdf_with_agent, document_path)
        result = to_result_payload(extraction)

        completed_job = await asyncio.to_thread(
            update_job,
            redis,
            settings.redis_job_prefix,
            job_id,
            {
                "status": "completed",
                "result": result,
                "error": None,
                "completedAt": extraction.extractedAt
            },
            settings.job_ttl_seconds
        )

        notification_url = job.data.get("notificationUrl") or settings.notification_webhook_url
        if notification_url:
            payload = build_notification_payload(completed_job or job_record, "completed", result=result)
            try:
                await asyncio.to_thread(send_notification, notification_url, payload)
            except Exception as error:
                print(f"Notification failed for job {job_id}: {error}")

        return result
    except Exception as error:
        await asyncio.to_thread(
            update_job,
            redis,
            settings.redis_job_prefix,
            job_id,
            {
                "status": "failed",
                "error": str(error),
                "completedAt": datetime.now(timezone.utc).isoformat()
            },
            settings.job_ttl_seconds
        )
        raise


async def run_worker() -> None:
    settings = load_settings()
    redis = redis_client(settings)
    shutdown_event = asyncio.Event()

    def handle_signal(_signum, _frame) -> None:
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    async def processor(job: Any, token: str) -> Dict[str, Any]:
        return await process_job(job, token, settings, redis)

    worker = Worker(
        settings.bullmq_queue_name,
        processor,
        {
            "connection": settings.redis_url
        }
    )

    print(
        f"Worker listening on queue '{settings.bullmq_queue_name}' and documents at '{settings.document_storage_dir}'"
    )

    try:
        await shutdown_event.wait()
    finally:
        await worker.close()
        redis.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
