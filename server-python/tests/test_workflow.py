import json
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

import app.tasks as tasks
from app.config import settings
from app.jobs import get_job
from app.main import app


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page()
    return doc.tobytes()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.routes.extract.enqueue_extraction", lambda job: None)
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_upload_requires_document(client):
    response = client.post("/extract", data={})
    assert response.status_code == 400
    assert response.json() == {"error": "A PDF document is required"}


def test_upload_rejects_bad_notification_url(client):
    response = client.post(
        "/extract", files={"document": ("a.pdf", _pdf_bytes())}, data={"notificationUrl": "nope"}
    )
    assert response.status_code == 400


def test_unknown_job_is_404(client):
    assert client.get("/jobs/missing").status_code == 404


def test_upload_creates_job_and_task_completes_it(client, monkeypatch):
    response = client.post(
        "/extract",
        files={"document": ("plan.pdf", _pdf_bytes())},
        data={"notificationUrl": "https://example.com/hook"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["queue"] == settings.queue_name
    assert Path(body["documentPath"]).is_file()

    job = client.get(body["statusUrl"]).json()
    assert job["status"] == "queued" and job["attempts"] == 0

    seen_partial = []
    notified = []

    def fake_extract(path, on_page):
        page = {"pageNumber": 1, "isPlan": True, "counts": {"wall": 2}}
        on_page(page)
        seen_partial.append(get_job(body["jobId"])["result"])
        return {"accepted": True, "pages": [page], "document": {"pageCount": 1, "planPageCount": 1},
                "totals": {"wall": 2}, "extractedAt": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr(tasks, "extract_pdf_with_agent", fake_extract)
    monkeypatch.setattr(tasks, "send_notification", lambda url, payload: notified.append((url, payload)))

    tasks.extract_document.run(job_id=body["jobId"])

    assert seen_partial[0]["partial"] is True
    assert seen_partial[0]["document"]["pagesProcessed"] == 1

    job = client.get(body["statusUrl"]).json()
    assert job["status"] == "completed" and job["attempts"] == 1
    assert job["result"]["accepted"] is True
    assert job["completedAt"] == "2026-01-01T00:00:00+00:00"

    url, payload = notified[0]
    assert url == "https://example.com/hook"
    assert payload["jobId"] == body["jobId"] and payload["status"] == "completed"

    saved = json.loads((Path(settings.extraction_output_dir) / f"{body['jobId']}.json").read_text())
    assert saved["jobId"] == body["jobId"]


def test_task_marks_job_failed_on_error(client, monkeypatch):
    body = client.post("/extract", files={"document": ("plan.pdf", _pdf_bytes())}).json()
    monkeypatch.setattr(tasks, "extract_pdf_with_agent", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        tasks.extract_document.run(job_id=body["jobId"])

    job = get_job(body["jobId"])
    assert job["status"] == "failed" and job["error"] == "boom" and job["completedAt"]


def test_task_fails_fast_when_job_record_missing():
    with pytest.raises(tasks.UnrecoverableError):
        tasks.extract_document.run(job_id="ghost")
