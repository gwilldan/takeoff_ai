import json
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

import app.tasks as tasks
from app.config import settings
from app.jobs import get_job
from app.main import app


def _pdf_bytes(pages: int = 2) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def _plan_page(index: int) -> dict:
    usage = {"calls": [{"step": f"classify_page_{index + 1}", "model": "m", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}],
             "totals": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    return {"pageNumber": index + 1, "isPlan": True, "counts": {"walls": 2}, "components": [], "tokenUsage": usage}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.routes.extract.enqueue_extraction", lambda job: None)
    with TestClient(app) as c:
        yield c


def _upload(client, **data):
    return client.post("/extract", files={"document": ("plan.pdf", _pdf_bytes())}, data=data)


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_cors_only_allows_frontend(client):
    allowed = client.options("/health", headers={"Origin": settings.frontend_origins[0], "Access-Control-Request-Method": "GET"})
    denied = client.options("/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert allowed.headers["access-control-allow-origin"] == settings.frontend_origins[0]
    assert "access-control-allow-origin" not in denied.headers


def test_upload_requires_document(client):
    response = client.post("/extract", data={})
    assert response.status_code == 400
    assert response.json() == {"error": "A PDF document is required"}


def test_upload_rejects_non_pdf(client):
    response = client.post("/extract", files={"document": ("plan.pdf", b"hello world")})
    assert response.status_code == 400
    assert response.json() == {"error": "Document must be a PDF"}
    assert not list(Path(settings.document_storage_dir).glob("*/source.pdf"))


def test_upload_rejects_bad_notification_url(client):
    assert _upload(client, notificationUrl="nope").status_code == 400


def test_unknown_job_is_404(client):
    assert client.get("/jobs/missing").status_code == 404


def test_job_is_served_in_camel_case(client):
    body = _upload(client, notificationUrl="https://example.com/hook").json()
    job = client.get(body["statusUrl"]).json()
    assert job == {
        "id": body["jobId"], "status": "queued", "createdAt": job["createdAt"], "updatedAt": job["updatedAt"],
        "documentPath": body["documentPath"], "notificationUrl": "https://example.com/hook", "attempts": 0,
    }


def test_pages_stream_into_the_job_then_finalize(client, monkeypatch):
    body = _upload(client, notificationUrl="https://example.com/hook").json()
    job_id = body["jobId"]
    dispatched = []
    notified = []

    monkeypatch.setattr(tasks, "chord", lambda header: lambda body_sig: dispatched.append((header, body_sig)))
    monkeypatch.setattr(tasks, "process_page", lambda pdf, index, tracker: _plan_page(index))
    monkeypatch.setattr(tasks, "notify_completion", notified.append)

    tasks.extract_document.run(job_id=job_id)

    job = client.get(body["statusUrl"]).json()
    assert job["status"] == "processing" and job["attempts"] == 1
    assert job["result"]["partial"] is True and job["result"]["pages"] == []
    assert job["result"]["document"] == {"pageCount": 2, "pagesProcessed": 0, "planPageCount": 0, "pdfPath": job["result"]["document"]["pdfPath"]}
    assert len(dispatched[0][0]) == 2

    # pages finish out of order; each is visible immediately
    second = tasks.extract_page.run(job_id, body["documentPath"], 1)
    job = client.get(body["statusUrl"]).json()
    assert job["result"]["document"]["pagesProcessed"] == 1 and job["result"]["totals"] == {"walls": 2}
    assert job["result"]["tokenUsage"]["totals"]["total_tokens"] == 15  # live, before finalize

    first = tasks.extract_page.run(job_id, body["documentPath"], 0)
    job = client.get(body["statusUrl"]).json()
    assert [p["pageNumber"] for p in job["result"]["pages"]] == [1, 2]

    tasks.finalize_extraction.run([second, first], job_id)

    job = client.get(body["statusUrl"]).json()
    assert job["status"] == "completed" and job["completedAt"]
    result = job["result"]
    assert result["partial"] is False and result["accepted"] is True
    assert result["totals"] == {"walls": 4}
    assert result["tokenUsage"]["totals"]["total_tokens"] == 30 and len(result["tokenUsage"]["calls"]) == 2

    assert notified[0].id == job_id
    saved = json.loads((Path(settings.extraction_output_dir) / f"{job_id}.json").read_text())
    assert saved["jobId"] == job_id and saved["accepted"] is True


def test_document_without_plans_is_rejected(client, monkeypatch):
    body = _upload(client).json()
    monkeypatch.setattr(tasks, "chord", lambda header: lambda sig: None)
    monkeypatch.setattr(tasks, "notify_completion", lambda job: None)
    tasks.extract_document.run(job_id=body["jobId"])

    skipped = {"pageNumber": 1, "isPlan": False, "skippedReason": "schedule", "components": []}
    tasks.finalize_extraction.run([skipped], body["jobId"])

    result = get_job(body["jobId"]).result
    assert result.accepted is False and result.rejection["reason"] == "not_a_construction_plan"


def test_failed_page_is_recorded_as_skipped_after_retries(client, monkeypatch):
    body = _upload(client).json()
    monkeypatch.setattr(tasks, "chord", lambda header: lambda sig: None)
    tasks.extract_document.run(job_id=body["jobId"])

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "process_page", boom)
    monkeypatch.setattr(tasks.extract_page, "max_retries", 0)

    out = tasks.extract_page.run(body["jobId"], body["documentPath"], 0)
    assert out["isPlan"] is False and "boom" in out["skippedReason"]
    assert get_job(body["jobId"]).result.pages[0]["skippedReason"] == out["skippedReason"]


def test_missing_document_fails_the_job_without_retry(client):
    body = _upload(client).json()
    Path(body["documentPath"]).unlink()

    with pytest.raises(tasks.UnrecoverableError):
        tasks.extract_document.run(job_id=body["jobId"])

    job = get_job(body["jobId"])
    assert job.status == "failed" and job.error == "Uploaded document is missing" and job.completed_at


def test_task_fails_fast_when_job_record_missing():
    with pytest.raises(tasks.UnrecoverableError):
        tasks.extract_document.run(job_id="ghost")
