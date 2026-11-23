import json
from pathlib import Path

import fitz
import pytest
from celery.exceptions import Retry
from fastapi.testclient import TestClient

import app.tasks as tasks
from app.config import settings
from app.jobs import get_job, get_pending_takeoff
from app.main import app


def _pdf_bytes(pages: int = 2) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def _usage(index: int) -> dict:
    return {"calls": [{"step": f"classify_page_{index + 1}", "model": "m", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}],
            "totals": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def _read(plan_pages: set[int]):
    def read(pdf, index, tracker):
        page = {"page_index": index, "is_plan": index in plan_pages, "plan_type": "floor_plan", "detections": [],
                "page_size_pt": {"width": 1000, "height": 500}, "token_usage": _usage(index), "timings": {"read_s": 1.0}}
        if index not in plan_pages:
            page["skipped_reason"] = "schedule"
        return page
    return read


def _results() -> dict:
    # A one-page PDF was sent, so the result is always its page 0.
    return {
        "id": "tk_1", "status": "succeeded", "schema_version": "2026-11-02", "metrics": {"Doors detected": 0},
        "pages": [{"page_index": 0, "image_size": {"width": 2000, "height": 1000},
                   "scale": {"meters_per_pixel": 0.01, "source": "drawing"},
                   "detections": [{"id": "w1", "class_name": "wall", "score": 0.9, "bbox": [0, 0, 500, 20],
                                   "polygon": [[0, 0], [500, 0], [500, 20], [0, 20]]}], "plans": []}],
        "wall_types": [{"id": "wt1", "pattern": "solid", "walls": 1, "length_m": 5.0, "external_length_m": 0}],
        "review": {"doors": 1, "windows": 0}, "assets": [],
    }


class FakeTakeoffApi:
    """QuantTakeoff with every takeoff succeeding on its second status check."""

    TERMINAL = tasks.takeoff_api.TERMINAL
    TakeoffApiError = tasks.takeoff_api.TakeoffApiError

    def __init__(self):
        self.created, self.checks, self.status_after = [], {}, "succeeded"

    def create_takeoff(self, pdf_path, page_index, **kwargs):
        self.created.append((pdf_path, page_index, kwargs))
        return f"tk_{page_index}"

    def get_takeoff(self, takeoff_id):
        self.checks[takeoff_id] = self.checks.get(takeoff_id, 0) + 1
        return {"status": "running" if self.checks[takeoff_id] == 1 else self.status_after}

    def get_results(self, takeoff_id):
        return _results()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.routes.extract.enqueue_extraction", lambda job: None)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def api(monkeypatch):
    fake = FakeTakeoffApi()
    monkeypatch.setattr(tasks, "takeoff_api", fake)
    monkeypatch.setattr(tasks.extract_page, "delay", lambda *args: None)
    monkeypatch.setattr(tasks.collect_takeoff, "apply_async", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "notify_completion", lambda job: None)
    return fake


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


def test_floor_plans_go_to_quanttakeoff_and_the_last_page_closes_the_job(client, api, monkeypatch):
    body = _upload(client).json()
    job_id, pdf = body["jobId"], body["documentPath"]
    monkeypatch.setattr(tasks, "read_page", _read(plan_pages={1}))
    notified = []
    monkeypatch.setattr(tasks, "notify_completion", notified.append)

    tasks.extract_document.run(job_id=job_id)
    job = client.get(body["statusUrl"]).json()
    assert job["status"] == "processing"
    assert job["result"]["document"] == {"page_count": 2, "pages_processed": 0, "plan_page_count": 0}

    # Page 2 is a floor plan: a takeoff starts and nothing is published yet.
    tasks.extract_page.run(job_id, pdf, 1)
    assert [c[:2] for c in api.created] == [(pdf, 1)] and get_pending_takeoff("tk_1")["job_id"] == job_id
    assert get_job(job_id).result.pages == []

    # Page 1 is not: published straight away, never sent.
    tasks.extract_page.run(job_id, pdf, 0)
    result = client.get(body["statusUrl"]).json()["result"]
    assert [p["page_index"] for p in result["pages"]] == [0] and result["pages"][0]["skipped_reason"] == "schedule"

    with pytest.raises(Retry):  # still running on the first check
        tasks.collect_takeoff.run("tk_1")
    tasks.collect_takeoff.run("tk_1")
    tasks.collect_takeoff.run("tk_1")  # a late callback after publishing does nothing
    assert get_pending_takeoff("tk_1") is None

    job = client.get(body["statusUrl"]).json()
    assert job["status"] == "completed" and job["completedAt"]
    result = job["result"]
    assert result["status"] == "succeeded" and result["accepted"] is True and result["schema_version"] == "2026-11-02"
    plan = result["pages"][1]
    assert plan["page_index"] == 1 and plan["takeoff_id"] == "tk_1" and plan["image_size"] == {"width": 2000, "height": 1000}
    assert plan["metrics"] == {"Doors detected": 0}
    assert plan["detections"][0]["length_m"] == 5.0 and plan["timings"]["takeoff_s"] >= 0
    assert result["metrics"]["counts"] == {"wall": 1} and result["metrics"]["wall_length_m"] == 5.0
    assert result["wall_types"][0]["page_index"] == 1 and result["review"] == {"doors": 1, "windows": 0}
    assert result["token_usage"]["totals"]["total_tokens"] == 30 and result["timings"]["elapsed_s"] >= 0

    assert notified[0].id == job_id
    saved = json.loads((Path(settings.extraction_output_dir) / f"{job_id}.json").read_text())
    assert saved["id"] == job_id and saved["accepted"] is True


def test_a_failed_takeoff_is_reported_on_its_page(client, api, monkeypatch):
    body = _upload(client).json()
    monkeypatch.setattr(tasks, "read_page", _read(plan_pages={0, 1}))
    api.status_after = "failed"
    tasks.extract_document.run(job_id=body["jobId"])
    tasks.extract_page.run(body["jobId"], body["documentPath"], 0)
    with pytest.raises(Retry):
        tasks.collect_takeoff.run("tk_0")
    tasks.collect_takeoff.run("tk_0")
    page = get_job(body["jobId"]).result.pages[0]
    assert page["is_plan"] and page["error"] == "takeoff failed: no reason given"


def test_document_without_floor_plans_is_rejected(client, api, monkeypatch):
    body = _upload(client).json()
    monkeypatch.setattr(tasks, "read_page", _read(plan_pages=set()))
    tasks.extract_document.run(job_id=body["jobId"])
    for index in (0, 1):
        tasks.extract_page.run(body["jobId"], body["documentPath"], index)

    job = get_job(body["jobId"])
    assert job.status == "completed" and api.created == []
    assert job.result.accepted is False and job.result.rejection["reason"] == "no_floor_plan"


def test_unreadable_page_is_skipped_after_retries(client, api, monkeypatch):
    body = _upload(client).json()
    tasks.extract_document.run(job_id=body["jobId"])

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "read_page", boom)
    monkeypatch.setattr(tasks.extract_page, "max_retries", 0)
    tasks.extract_page.run(body["jobId"], body["documentPath"], 0)
    page = get_job(body["jobId"]).result.pages[0]
    assert page["is_plan"] is False and "boom" in page["skipped_reason"]


def test_callback_only_collects_takeoffs_we_are_waiting_on(client, api, monkeypatch):
    collected = []
    monkeypatch.setattr(tasks.collect_takeoff, "delay", collected.append)
    body = _upload(client).json()
    monkeypatch.setattr(tasks, "read_page", _read(plan_pages={0}))
    tasks.extract_document.run(job_id=body["jobId"])
    tasks.extract_page.run(body["jobId"], body["documentPath"], 0)

    assert client.post("/webhooks/quanttakeoff", json={"event": "takeoff.succeeded", "id": "tk_unknown"}).status_code == 202
    client.post("/webhooks/quanttakeoff", content=b"not json")
    client.post("/webhooks/quanttakeoff", json={"event": "takeoff.succeeded", "data": {"id": "tk_0"}})
    assert collected == ["tk_0"]


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


def test_finished_job_is_kept_for_training(client, api, monkeypatch, tmp_path):
    assert client.post("/jobs/nope/training", json={"pages": [{"page_index": 0, "detections": []}]}).status_code == 503
    monkeypatch.setattr(settings, "training_data_dir", str(tmp_path))
    body = _upload(client).json()
    job_id, pdf = body["jobId"], body["documentPath"]
    monkeypatch.setattr(tasks, "read_page", _read(plan_pages={1}))
    tasks.extract_document.run(job_id=job_id)
    for index in (0, 1):
        tasks.extract_page.run(job_id, pdf, index)
    with pytest.raises(Retry):
        tasks.collect_takeoff.run("tk_1")
    tasks.collect_takeoff.run("tk_1")

    # Finishing leaves the drawing and the engine's plan pages as a draft, never as a label.
    assert (tmp_path / "sources" / f"{job_id}.pdf").read_bytes() == Path(pdf).read_bytes()
    draft = json.loads((tmp_path / "drafts" / f"{job_id}.json").read_text())
    assert [p["page_index"] for p in draft["pages"]] == [1] and draft["pages"][0]["detections"][0]["class_name"] == "wall"
    assert not (tmp_path / "labels").exists()

    room = {"class_name": "space", "polygon": [[10, 10], [200, 10], [200, 100]], "name": "KITCHEN"}
    assert client.post(f"/jobs/{job_id}/training", json={"pages": [{"page_index": 0, "detections": [room]}]}).status_code == 400
    saved = client.post(f"/jobs/{job_id}/training", json={"pages": [{"page_index": 1, "detections": [room, {**room, "class_name": "note"}]}]})
    assert saved.status_code == 201 and saved.json()["detections"] == 1
    label = json.loads((tmp_path / "labels" / f"{job_id}.json").read_text())
    page = label["pages"][0]
    assert label["reviewed"] is True and page["image_size"] == {"width": 1000, "height": 500}
    assert page["detections"] == [room]
