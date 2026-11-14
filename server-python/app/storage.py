"""Uploaded documents live at `<DOCUMENT_STORAGE_DIR>/<jobId>/source.pdf` on a shared volume."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings

PDF_MAGIC = b"%PDF-"


class UploadTooLarge(Exception):
    pass


class NotAPdf(Exception):
    pass


def job_dir(job_id: str) -> Path:
    return Path(settings.document_storage_dir) / job_id


def document_path(job_id: str) -> Path:
    return job_dir(job_id) / "source.pdf"


def store_document(job_id: str, stream, chunk_size: int = 1024 * 1024) -> Path:
    """Stream the upload to disk, refusing non-PDFs and anything over the size limit."""
    target = document_path(job_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    head = stream.read(1024)
    if PDF_MAGIC not in head:  # the spec allows the header anywhere in the first 1024 bytes
        raise NotAPdf()

    written = len(head)
    with target.open("wb") as out:
        out.write(head)
        while chunk := stream.read(chunk_size):
            written += len(chunk)
            if written > settings.max_upload_bytes:
                raise UploadTooLarge()
            out.write(chunk)

    return target


def remove_document(job_id: str) -> None:
    shutil.rmtree(job_dir(job_id), ignore_errors=True)


def resolve_document(raw_path: str) -> Path:
    """Validate a job's document path before the worker touches it."""
    path = Path(raw_path).expanduser().resolve()
    root = Path(settings.document_storage_dir).expanduser().resolve()

    if not path.is_relative_to(root):
        raise ValueError("Document path is outside the upload volume")
    if not path.is_file():
        raise ValueError("Uploaded document is missing")

    return path
