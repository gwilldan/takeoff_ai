'use client';

import { ChangeEvent, FormEvent, useEffect, useState } from 'react';
import { PdfPreview } from '../components/pdf-preview';

type JobStatus = 'queued' | 'processing' | 'completed' | 'failed';

type ExtractResponse = {
  jobId: string;
  status: JobStatus;
  statusUrl: string;
  documentPath: string;
  queue: string;
};

type ExtractionResult = {
  scale?: string;
  scale_ratio?: number;
  confidence?: string;
  metadata?: {
    pdf_path?: string;
    page_size_pts?: number[];
    text_span_count?: number;
    line_segment_count?: number;
    curve_count?: number;
  };
  walls?: Array<{ id: string; length_mm: number; start_pts: number[]; end_pts: number[] }>;
  dimensions?: Array<{ value_mm: number; text: string; confidence: string }>;
  rooms?: Array<{
    id: string;
    name: string;
    display_name?: string;
    area_m2?: number | null;
    area_source?: string;
    dimensions_mm?: Record<string, number>;
  }>;
  openings?: Array<{ kind: string; reference: string }>;
  notes?: string[];
  token_usage?: {
    totals?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  };
  extractedAt?: string;
};

type JobRecord = {
  id: string;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  documentPath?: string;
  notificationUrl?: string;
  attempts: number;
  completedAt?: string;
  error?: string;
  result?: ExtractionResult;
};

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:4000';

function formatFileSize(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function Page() {
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [notificationUrl, setNotificationUrl] = useState('');
  const [job, setJob] = useState<JobRecord | null>(null);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);

  const isProcessing =
    loading || (job !== null && job.status !== 'completed' && job.status !== 'failed');

  useEffect(() => {
    if (!job || job.status === 'completed' || job.status === 'failed') {
      return;
    }

    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/jobs/${job.id}`);
        if (!response.ok) {
          return;
        }

        const nextJob = (await response.json()) as JobRecord;
        setJob(nextJob);
      } catch {
        // Keep polling quietly if the backend is still coming up.
      }
    }, 2500);

    return () => window.clearInterval(timer);
  }, [job]);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (isProcessing) {
      return;
    }

    const nextFile = event.target.files?.[0] ?? null;
    setDocumentFile(nextFile);
    setJob(null);
    setMessage('');
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (isProcessing) {
      return;
    }

    if (!documentFile) {
      setMessage('Please choose a PDF file first.');
      return;
    }

    setLoading(true);
    setMessage('');

    try {
      const formData = new FormData();
      formData.append('document', documentFile);

      if (notificationUrl.trim()) {
        formData.append('notificationUrl', notificationUrl.trim());
      }

      const response = await fetch(`${apiBaseUrl}/extract`, {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || 'Failed to start extraction');
      }

      const data = (await response.json()) as ExtractResponse;
      setJob({
        id: data.jobId,
        status: data.status,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        documentPath: data.documentPath,
        notificationUrl: notificationUrl || undefined,
        attempts: 0
      });
      setMessage(`Job ${data.jobId} queued on ${data.queue}. Polling for results…`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unexpected error');
    } finally {
      setLoading(false);
    }
  }

  function buttonLabel() {
    if (loading) {
      return 'Starting…';
    }
    if (job?.status === 'queued') {
      return 'Queued…';
    }
    if (job?.status === 'processing') {
      return 'Extracting…';
    }
    return 'Start extraction';
  }

  return (
    <main className="shell">
      <div className="workspace">
        <header className="hero card">
          <div className="hero-copy">
            <p className="eyebrow">Takeoff AI</p>
            <h1>Upload a PDF, preview every page, and start extraction in one place.</h1>
            <p>
              The frontend renders the document locally with PDF.js, then sends the same file to
              the Express workflow where it is stored in the shared volume and queued for the
              Python BullMQ worker.
            </p>
          </div>
          <div className="hero-meta">
            <span className="badge">Next.js + TypeScript</span>
            <span className="badge">PDF.js multi-page preview</span>
            <span className="badge">Express + BullMQ + Python worker</span>
          </div>
        </header>

        <section className="layout">
          <div className="card card--padded">
            <div className="section-head">
              <div>
                <p className="eyebrow">Workflow</p>
                <h2>Upload and queue</h2>
              </div>
            </div>

            <form className="form" onSubmit={handleSubmit}>
              <label className="field" htmlFor="document">
                <span>PDF file</span>
                <input
                  id="document"
                  type="file"
                  accept="application/pdf"
                  onChange={handleFileChange}
                  disabled={isProcessing}
                  required
                />
              </label>

              <label className="field" htmlFor="notificationUrl">
                <span>Notification webhook</span>
                <input
                  id="notificationUrl"
                  value={notificationUrl}
                  onChange={(event) => setNotificationUrl(event.target.value)}
                  placeholder="https://example.com/webhook"
                  disabled={isProcessing}
                />
              </label>

              <div className="actions">
                <button type="submit" className="button-primary" disabled={isProcessing}>
                  {buttonLabel()}
                </button>
                <p className="helper">
                  {isProcessing
                    ? 'Extraction in progress — polling every 2.5s until complete.'
                    : 'The document is persisted first, then BullMQ starts the worker job.'}
                </p>
              </div>
            </form>

            {documentFile ? (
              <div className="file-summary">
                <strong>{documentFile.name}</strong>
                <span>{formatFileSize(documentFile.size)}</span>
              </div>
            ) : null}

            {message ? <p className="notice">{message}</p> : null}

            {job ? (
              <div className="status-card">
                <div className="section-head compact">
                  <div>
                    <p className="eyebrow">Job status</p>
                    <h2>Extraction progress</h2>
                  </div>
                  <span className="pill">{job.status}</span>
                </div>

                <div className="status-grid">
                  <div>
                    <span>Job ID</span>
                    <strong>{job.id}</strong>
                  </div>
                  <div>
                    <span>Attempts</span>
                    <strong>{job.attempts}</strong>
                  </div>
                  {job.documentPath ? (
                    <div className="status-wide">
                      <span>Document path</span>
                      <strong>{job.documentPath}</strong>
                    </div>
                  ) : null}
                </div>

                {job.result ? (
                  <div className="result">
                    <h3>Extraction result</h3>
                    <p>
                      Scale {job.result.scale ?? 'unknown'} · confidence{' '}
                      {job.result.confidence ?? 'n/a'} · extracted at{' '}
                      {job.result.extractedAt ?? job.completedAt ?? 'unknown'}
                    </p>
                    <div className="result-summary">
                      <span>{job.result.walls?.length ?? 0} walls</span>
                      <span>{job.result.rooms?.length ?? 0} rooms</span>
                      <span>{job.result.dimensions?.length ?? 0} dimensions</span>
                      <span>{job.result.openings?.length ?? 0} openings</span>
                      {job.result.token_usage?.totals ? (
                        <span>{job.result.token_usage.totals.total_tokens} tokens</span>
                      ) : null}
                    </div>
                    {job.result.rooms && job.result.rooms.length > 0 ? (
                      <ul className="result-list">
                        {job.result.rooms.map((room) => (
                          <li key={room.id}>
                            {room.display_name ?? room.name}
                            {room.area_m2 != null ? ` — ${room.area_m2} m²` : ''}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    <pre>{JSON.stringify(job.result, null, 2)}</pre>
                  </div>
                ) : null}

                {job.error ? <p className="error">{job.error}</p> : null}
              </div>
            ) : null}
          </div>

          <PdfPreview file={documentFile} />
        </section>
      </div>
    </main>
  );
}
