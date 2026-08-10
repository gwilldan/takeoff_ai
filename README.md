# Takeoff AI

Takeoff AI is an AI application for automating the production of civil engineering takeoffs from PDF documents.

Users upload a plan PDF, preview it in the browser, and start a background extraction workflow that stores the file, queues processing, and sends a notification when the job is done.

## Stack

- Frontend: Next.js + TypeScript
- Backend: Express + TypeScript
- Worker: Python + BullMQ
- Preview: PDF.js
- Storage: shared Docker volume
- Queue: Redis
- Database: Postgres

## Folder Structure

```text
.
├── frontend/
│   ├── app/
│   │   ├── globals.css
│   │   ├── layout.tsx
│   │   └── page.tsx
│   ├── components/
│   │   ├── pdf-page.tsx
│   │   └── pdf-preview.tsx
│   ├── lib/
│   │   └── pdfjs.ts
│   ├── public/
│   ├── Dockerfile
│   └── package.json
├── server/
│   ├── src/
│   ├── worker/
│   ├── compose.yaml
│   ├── compose.dev.yaml
│   ├── Dockerfile
│   └── package.json
└── README.md
```

## Workflow

1. The frontend uploads a PDF and renders the pages locally with PDF.js.
2. The Express API stores the file in the shared volume and creates a BullMQ job.
3. The Python worker processes the document and sends a notification when it finishes.
4. Postgres and Redis support the workflow behind the scenes.

## Run

### Development

```bash
docker compose -f server/compose.dev.yaml up --build
```

This starts the frontend, API, worker, Postgres, and Redis with development build targets and file watching.

### Production

```bash
docker compose -f server/compose.yaml up --build
```

This uses the production targets for the frontend, API, and worker containers.

## API Notes

See `server/README.md` for the backend endpoints and service-specific run instructions.
