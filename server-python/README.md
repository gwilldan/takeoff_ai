# PDF Extraction Backend (Python)

FastAPI API + Celery worker for the PDF takeoff workflow. Uploaded PDFs are stored on a shared volume, queued on Redis, processed by the Python extraction pipeline, and a webhook is called when the job finishes.

## What runs here

1. A FastAPI API (`app/main.py`) that accepts uploads and serves job state.
2. A Celery worker (`app/tasks.py`) that runs `app/extractor` one page at a time.
3. Redis as the Celery broker and the job-state store.
4. Postgres for persistence (production compose only).

Both processes are built from the same image and share `app/config.py`.

## API

### `POST /extract`

`multipart/form-data` with:

- `document`: PDF file to process (max 50 MB, must start with a PDF header)
- `notificationUrl`: optional webhook to call after the worker finishes

Returns `202` with `{ jobId, status, statusUrl, documentPath, queue }`.

### `GET /jobs/{jobId}`

Returns the job record (`app/models.py`). While processing, `result.partial` is `true` and `result.pages`, `result.totals` and `result.document.pagesProcessed` grow as each page finishes. On completion `partial` is `false` and `accepted`, `tokenUsage` and `extractedAt` are set.

### `GET /health`

Simple health check.

## Job lifecycle

`queued` → `processing` → `completed` | `failed`

Extraction is a Celery chord: `extract-document` validates the upload and fans out one `extract-page` task per page; `finalize-extraction` runs when all pages are in. Each page is published into the job record as soon as it finishes, so the frontend annotates page by page. Pages retry independently with exponential backoff (`QUEUE_ATTEMPTS`, `QUEUE_BACKOFF_DELAY_MS`); a page that still fails is recorded as skipped. Unrecoverable errors (not a PDF on disk, missing job record) fail the job immediately. Final results are also written to `EXTRACTION_OUTPUT_DIR/<jobId>.json`.

Redis is both the Celery broker and result backend; the backend is only used to coordinate the chord and expires results after an hour.

CORS allows only `FRONTEND_ORIGIN` (comma-separated for several).

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
uv run fastapi dev app/main.py --port 4000
uv run celery -A app.celery_app worker --loglevel=info --concurrency=1
```

Requires a Redis at `REDIS_URL`. Raise `WORKER_CONCURRENCY` to process pages of one document in parallel.
