export const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:4000';

export type JobStatus = 'queued' | 'processing' | 'completed' | 'failed';

export type ExtractionResult = {
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
  result?: ExtractionResult;
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
