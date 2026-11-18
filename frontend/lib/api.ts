import type { TakeoffDocument } from './annotations/extraction-import';

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
