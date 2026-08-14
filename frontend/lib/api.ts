export const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:4000';

export type JobStatus = 'queued' | 'processing' | 'completed' | 'failed';

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