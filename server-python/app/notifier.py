import logging
from typing import Any

import httpx

from app.config import settings
from app.models import JobRecord

log = logging.getLogger(__name__)


def notify_completion(job: JobRecord) -> None:
    """POST the finished job to its webhook (or the global one); never raises."""
    url = job.notification_url or settings.notification_webhook_url
    if not url or job.result is None:
        return

    payload: dict[str, Any] = {
        "jobId": job.id,
        "status": job.status,
        "source": {"documentPath": job.document_path},
        "completedAt": job.completed_at,
        "result": job.result.to_wire(),
    }

    last_error: Exception | None = None
    for _ in range(3):
        try:
            httpx.post(url, json=payload, timeout=15).raise_for_status()
            return
        except Exception as exc:
            last_error = exc

    log.warning("Notification failed for job %s: %s", job.id, last_error)
