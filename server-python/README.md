# PDF Extraction Backend (Python)

FastAPI API + Celery worker for the PDF takeoff workflow. Uploaded PDFs are stored on a shared volume, queued on Redis, processed by the Python extraction pipeline, and a webhook is called when the job finishes.

## What runs here

1. A FastAPI API (`app/main.py`) that accepts uploads and serves job state.
2. A Celery worker (`app/tasks.py`) that consumes the queue and runs `app/extractor`.
3. Redis as the Celery broker and the job-state store.
4. Postgres for persistence (production compose only).

Both processes are built from the same image and share `app/config.py`.

## API

### `POST /extract`

`multipart/form-data` with:

- `document`: PDF file to process (max 50 MB)
- `notificationUrl`: optional webhook to call after the worker finishes

Returns `202` with `{ jobId, status, statusUrl, documentPath, queue }`.

### `GET /jobs/{jobId}`

Returns the job record. While processing, `result.partial` is `true` and `result.pages` fills in page by page; on completion the full extraction result is returned.

### `GET /health`

Simple health check.

## Job lifecycle

`queued` → `processing` → `completed` | `failed`

Failed attempts are retried by Celery with exponential backoff (`QUEUE_ATTEMPTS`, `QUEUE_BACKOFF_DELAY_MS`). Unrecoverable errors (missing document, missing job record) fail immediately. Results are also written to `EXTRACTION_OUTPUT_DIR/<jobId>.json`.

## Run with Docker

Development (API, worker, Redis with file sync):

```bash
docker compose -f server-python/compose.dev.yaml up --build
```

Production:

```bash
docker compose -f server-python/compose.yaml up --build
```

## Run locally without Docker

```bash
cd server-python
cp .env.example .env   # fill in DASHSCOPE_API_KEY, set DOCUMENT_STORAGE_DIR / EXTRACTION_OUTPUT_DIR to writable paths
uv sync
uv run uvicorn app.main:app --reload --port 4000
uv run celery -A app.celery_app worker --loglevel=info --concurrency=1
```

Requires a Redis at `REDIS_URL`.
