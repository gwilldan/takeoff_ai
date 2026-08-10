# PDF Extraction Backend

This backend powers the PDF takeoff workflow for the civil engineering automation app. It receives uploaded PDFs, stores them in a shared volume, queues work with BullMQ, processes jobs in Python, and sends a notification when the job is done.

## What runs here

1. An Express API written in TypeScript.
2. A Python BullMQ worker that reads queued jobs and extracts PDF data.
3. A Next.js frontend in `../frontend` that renders uploads with PDF.js and starts the workflow.
4. Postgres for persistence and Redis for queueing.

## API

### `POST /extract`

Uploads a PDF, stores it in the shared volume, and enqueues a background job.

Request:

```bash
multipart/form-data
```

Fields:

- `document`: PDF file to process
- `notificationUrl`: optional webhook to call after the worker finishes

### `GET /jobs/:jobId`

Returns the current job state, including the extracted result once the worker completes.

### `GET /health`

Simple health check.

## Start Workflow

From the repository root:

### Development

```bash
docker compose -f server/compose.dev.yaml up --build
```

This starts the API, the Python worker, the Next.js app, Postgres, and Redis with live file sync for the frontend, backend, and worker code.

### Production

```bash
docker compose -f server/compose.yaml up --build
```

This starts the production build targets for the API, the worker, the frontend app, Postgres, and Redis.

## Local Development Without Docker

API:

```bash
cd server
npm install
npm run dev
```

Worker:

```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r worker/requirements.txt
python worker/main.py
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```
