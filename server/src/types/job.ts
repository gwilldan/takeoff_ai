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

export interface FloorPlanExtractionResult {
  scale?: string;
  scale_ratio?: number;
  confidence?: string;
  metadata?: Record<string, unknown>;
  walls?: unknown[];
  dimensions?: unknown[];
  rooms?: unknown[];
  openings?: unknown[];
  drawing_profile?: Record<string, unknown>;
  notes?: string[];
  token_usage?: Record<string, unknown>;
  extractedAt?: string;
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
  result?: FloorPlanExtractionResult;
  error?: string;
  completedAt?: string;
}
