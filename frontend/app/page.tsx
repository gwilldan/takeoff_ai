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
  result?: {
    pageCount: number;
    text: string;
    extractedAt: string;
  };
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
    const nextFile = event.target.files?.[0] ?? null;
    setDocumentFile(nextFile);
    setJob(null);
    setMessage('');
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

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
      setMessage(`Job ${data.jobId} queued on ${data.queue}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unexpected error');
    } finally {
      setLoading(false);
    }
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
                />
              </label>

              <div className="actions">
                <button type="submit" className="button-primary" disabled={loading}>
                  {loading ? 'Starting...' : 'Start extraction'}
                </button>
                <p className="helper">
                  The document is persisted first, then BullMQ starts the worker job.
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
                      {job.result.pageCount} pages extracted at {job.result.extractedAt}
                    </p>
                    <pre>{job.result.text || 'No text extracted.'}</pre>
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
