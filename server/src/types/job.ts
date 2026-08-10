export type JobStatus = 'queued' | 'processing' | 'completed' | 'failed';

export interface ExtractRequestBody {
  documentUrl?: string;
  documentPath?: string;
  notificationUrl?: string;
  metadata?: Record<string, unknown>;
}

export interface ExtractedTable {
  rows: string[][];
}

export interface ExtractedPage {
  pageNumber: number;
  text: string;
  tables: ExtractedTable[];
}

export interface PdfExtractionResult {
  pageCount: number;
  text: string;
  pages: ExtractedPage[];
  extractedAt: string;
}

export interface JobRecord {
  id: string;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  documentUrl?: string;
  documentPath?: string;
  notificationUrl?: string;
  metadata?: Record<string, unknown>;
  attempts: number;
  result?: PdfExtractionResult;
  error?: string;
  completedAt?: string;
}
