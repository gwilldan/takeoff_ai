# PDF Extraction Backend (Python)

FastAPI API + Celery worker for the PDF takeoff workflow. Uploaded PDFs are stored on a shared volume and queued on Redis; each page is classified, floor plans are measured by the QuantTakeoff API, and a webhook is called when the job finishes.

## What runs here

1. A FastAPI API (`app/main.py`) that accepts uploads and serves job state.
2. A Celery worker (`app/tasks.py`) that reads each page with `app/extractor` and sends floor plans to QuantTakeoff (`app/takeoff_api.py`).
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

Returns the job record (`app/models.py`). `result` is QuantTakeoff's takeoff result format with our fields beside theirs. While processing, `result.status` is `running` and `result.pages` and `result.document.pages_processed` grow as each page finishes. On completion `status` is `succeeded` and `accepted` and `timings.elapsed_s` are set.

### `POST /webhooks/quanttakeoff`

QuantTakeoff's completion callback. It only triggers collection of a takeoff we are waiting on; the result is always fetched from the API with our key.

### `GET /health`

Simple health check.

## Job lifecycle

`queued` → `processing` → `completed` | `failed`

`extract-document` fans out one `extract-page` task per page. Each page is classified; a page that is not a floor plan is published at once, a floor plan is posted to QuantTakeoff as a one-page PDF. `collect-takeoff` fetches each finished takeoff, on QuantTakeoff's callback or by polling every 10 seconds, merges it with what we read, and publishes the page. The page published last closes the job, writes `EXTRACTION_OUTPUT_DIR/<jobId>.json` and calls the webhook. Pages retry independently (`QUEUE_ATTEMPTS`, `QUEUE_BACKOFF_DELAY_MS`); a page that still fails is recorded with its reason.

## Extraction

The full walkthrough is in [docs/extraction-pipeline.md](../docs/extraction-pipeline.md).

1. `cache.py` parses the page: text spans (OCR via Tesseract on scans), vector lines, image coverage.
2. `classify.py` skips empty pages, sections and schedules, then asks the vision model whether the page is a floor plan. This is the only model call.
3. `calibrate.py` checks the stated scale against the printed dimensions and dimension chains: confirms it, overrules it when the sheet was resized, or measures it when none is stated. A checked scale replaces QuantTakeoff's when theirs is only an estimate.
4. `grid.py` reads the setting-out grid for snapping in the editor.
5. QuantTakeoff measures walls, doors, windows and rooms; `pipeline.complete_page` keeps its result whole and adds wall lengths, the grid and the scale used.

Test with `uv run pytest`; neither QuantTakeoff nor the model is called. The OCR test needs Tesseract, which the Docker image installs.

Redis is the Celery broker and the job store; there is no result backend.

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
cp .env.example .env   # fill in DASHSCOPE_API_KEY and QUANTTAKEOFF_API_KEY, set DOCUMENT_STORAGE_DIR / EXTRACTION_OUTPUT_DIR to writable paths
uv sync
uv run fastapi dev app/main.py --port 4000
uv run celery -A app.celery_app worker --loglevel=info --concurrency=2
```

Requires a Redis at `REDIS_URL`. `WORKER_CONCURRENCY` (default 2) is how many pages are read in parallel; takeoffs run on QuantTakeoff and hold no worker. Set `PUBLIC_API_URL` to an address QuantTakeoff can reach to receive its callbacks; without it, finished takeoffs are found by polling.
