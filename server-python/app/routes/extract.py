from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.config import settings
from app.jobs import create_job, update_job
from app.storage import UploadTooLarge, remove_document, store_document
from app.tasks import enqueue_extraction

router = APIRouter(prefix="/extract", tags=["extract"])
_url = TypeAdapter(HttpUrl)


def _read_notification_url(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    try:
        return str(_url.validate_python(value.strip()))
    except ValidationError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "notificationUrl must be a valid URL")


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def start_extraction(
    document: UploadFile | None = File(default=None),
    notificationUrl: str | None = Form(default=None),
):
    if document is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A PDF document is required")

    notification_url = _read_notification_url(notificationUrl)
    job_id = str(uuid4())

    try:
        path = store_document(job_id, document.file)
    except UploadTooLarge:
        remove_document(job_id)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")

    job = create_job(job_id, str(path), notification_url)

    try:
        enqueue_extraction(job)
    except Exception as exc:
        update_job(job_id, {"status": "failed", "error": str(exc)})
        remove_document(job_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            {"error": "Failed to start extraction workflow", "details": str(exc)},
        )

    return {
        "jobId": job_id,
        "status": job["status"],
        "statusUrl": f"/jobs/{job_id}",
        "documentPath": job["documentPath"],
        "queue": settings.queue_name,
    }
