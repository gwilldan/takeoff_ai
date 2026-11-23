import type { TakeoffDocument, TrainingPage } from './annotations/extraction-import';

export const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:4000';

export type JobStatus = 'queued' | 'processing' | 'completed' | 'failed';

export type JobRecord = {
  id: string;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  documentPath?: string;
  notificationUrl?: string;
  attempts: number;
  completedAt?: string;
  error?: string;
  result?: TakeoffDocument;
};

export type StartExtractionResponse = {
  jobId: string;
  status: JobStatus;
  statusUrl: string;
  documentPath: string;
  queue: string;
};

export async function startExtraction(
  file: File,
  notificationUrl?: string
): Promise<StartExtractionResponse> {
  const formData = new FormData();
  formData.append('document', file);

  if (notificationUrl && notificationUrl.trim()) {
    formData.append('notificationUrl', notificationUrl.trim());
  }

  const response = await fetch(`${apiBaseUrl}/extract`, { method: 'POST', body: formData });

  if (!response.ok) {
    throw new Error((await response.text()) || 'Failed to start extraction');
  }

  return (await response.json()) as StartExtractionResponse;
}

export async function fetchJob(jobId: string): Promise<JobRecord> {
  const response = await fetch(`${apiBaseUrl}/jobs/${jobId}`);

  if (!response.ok) {
    throw new Error(`Job ${jobId} returned ${response.status}`);
  }

  return (await response.json()) as JobRecord;
}

/** Keep corrected layers as labels for training the takeoff model. */
export async function saveTrainingLabels(
  jobId: string,
  pages: TrainingPage[]
): Promise<{ saved: string; pages: number; detections: number }> {
  const response = await fetch(`${apiBaseUrl}/jobs/${jobId}/training`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pages })
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `Saving for training failed (${response.status})`);
  }

  return (await response.json()) as { saved: string; pages: number; detections: number };
}
