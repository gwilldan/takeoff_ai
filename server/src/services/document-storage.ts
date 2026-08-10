import { mkdir, rm } from 'fs/promises';
import path from 'path';
import { env } from '../config/env';

export interface UploadContext {
  jobId: string;
  jobDir: string;
  documentPath: string;
}

export function createUploadContext(jobId: string): UploadContext {
  const jobDir = path.join(env.DOCUMENT_STORAGE_DIR, jobId);

  return {
    jobId,
    jobDir,
    documentPath: path.join(jobDir, 'source.pdf')
  };
}

export async function ensureUploadContext(jobId: string): Promise<UploadContext> {
  const context = createUploadContext(jobId);
  await mkdir(context.jobDir, { recursive: true });
  return context;
}

export async function removeUploadContext(jobId: string) {
  await rm(path.join(env.DOCUMENT_STORAGE_DIR, jobId), { recursive: true, force: true });
}
