"""
Serve a trained model behind the same API the backend already calls on
QuantTakeoff, so switching engines is a change of URL:

    POST /v1.1/takeoffs                    multipart `file` (PDF or image), optional `webhook_url`, `scale`
    GET  /v1.1/takeoffs/{id}               status: queued, running, succeeded or failed
    GET  /v1.1/takeoffs/{id}/results       the takeoff result
    GET  /v1.1/health

Jobs run one at a time on the model's device, in the order they arrive. An
`Idempotency-Key` header returns the job it first created. When
TAKEOFF_MODEL_API_KEY is set, requests must send it as a bearer token.

    takeoff-serve --checkpoint runs/convnext-v1/best.pt --port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

PREFIX = "/v1.1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_app(predictor, api_key: str | None = None, workdir: str | None = None) -> FastAPI:
    """The API around any object with `predict(path, pages, denominator, document_id)`."""
    app = FastAPI(title="takeoff-model")
    jobs: dict[str, dict[str, Any]] = {}
    idempotent: dict[str, str] = {}
    lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1)  # one job at a time on the model's device
    folder = Path(workdir or tempfile.mkdtemp(prefix="takeoff-model-"))

    def error(status: int, code: str, message: str):
        raise HTTPException(status, {"error": {"code": code, "message": message, "request_id": f"req_{uuid.uuid4().hex}"}})

    @app.exception_handler(HTTPException)
    async def shaped(_: Request, exc: HTTPException):
        return JSONResponse(exc.detail, status_code=exc.status_code)

    def authorise(authorization: str | None):
        if api_key and authorization != f"Bearer {api_key}":
            error(401, "unauthorized", "Missing or invalid API key.")

    def view(job: dict[str, Any]) -> dict[str, Any]:
        base = f"{PREFIX}/takeoffs/{job['id']}"
        return {"id": job["id"], "status": job["status"], "created_at": job["created_at"],
                "links": {"self": base, "results": f"{base}/results"}, "metadata": {}, "error": job.get("error")}

    def run(job_id: str, path: Path, denominator: float | None, webhook_url: str | None):
        job = jobs[job_id]
        job["status"] = "running"
        try:
            job["result"] = predictor.predict(path, None, denominator, job_id)
            job["status"] = "succeeded"
        except Exception as exc:  # the job reports it; the server keeps serving
            job["status"], job["error"] = "failed", str(exc)
        finally:
            path.unlink(missing_ok=True)
        if webhook_url:
            body = json.dumps({"event": f"takeoff.{job['status']}", "id": job_id}).encode()
            try:
                urllib.request.urlopen(urllib.request.Request(webhook_url, body, {"Content-Type": "application/json"}), timeout=10)
            except Exception:
                pass  # callers poll as well

    @app.get(f"{PREFIX}/health")
    def health():
        return {"status": "ok", "queued": sum(j["status"] in ("queued", "running") for j in jobs.values())}

    @app.post(f"{PREFIX}/takeoffs", status_code=202)
    async def create(file: UploadFile = File(...), webhook_url: str | None = Form(None), scale: float | None = Form(None),
                     authorization: str | None = Header(None), idempotency_key: str | None = Header(None)):
        authorise(authorization)
        with lock:
            if idempotency_key and idempotency_key in idempotent:
                return view(jobs[idempotent[idempotency_key]])
            job_id = f"tk_{uuid.uuid4().hex[:24]}"
            jobs[job_id] = {"id": job_id, "status": "queued", "created_at": _now()}
            if idempotency_key:
                idempotent[idempotency_key] = job_id
        suffix = Path(file.filename or "upload.pdf").suffix.lower() or ".pdf"
        path = folder / f"{job_id}{suffix}"
        path.write_bytes(await file.read())
        executor.submit(run, job_id, path, scale, webhook_url)
        return view(jobs[job_id])

    def find(takeoff_id: str) -> dict[str, Any]:
        if takeoff_id not in jobs:
            error(404, "not_found", f"No takeoff '{takeoff_id}'.")
        return jobs[takeoff_id]

    @app.get(f"{PREFIX}/takeoffs/{{takeoff_id}}")
    def status(takeoff_id: str, authorization: str | None = Header(None)):
        authorise(authorization)
        return view(find(takeoff_id))

    @app.get(f"{PREFIX}/takeoffs/{{takeoff_id}}/results")
    def results(takeoff_id: str, authorization: str | None = Header(None)):
        authorise(authorization)
        job = find(takeoff_id)
        if job["status"] != "succeeded":
            return {"id": takeoff_id, "status": job["status"], "pages": [], "metrics": {}, "assets": [], "error": job.get("error")}
        return job["result"]

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", default=os.environ.get("TAKEOFF_MODEL_CHECKPOINT"), required="TAKEOFF_MODEL_CHECKPOINT" not in os.environ)
    parser.add_argument("--device", default=os.environ.get("TAKEOFF_MODEL_DEVICE"))
    parser.add_argument("--config", default=os.environ.get("TAKEOFF_MODEL_CONFIG"), help="prediction and post-processing settings")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    import uvicorn

    from takeoff_model.predict import Predictor

    app = create_app(Predictor(args.checkpoint, args.device, args.config), os.environ.get("TAKEOFF_MODEL_API_KEY"))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
