import { randomUUID } from 'crypto';
import multer, { diskStorage } from 'multer';
import { Router, type NextFunction, type Request, type Response } from 'express';
import { z } from 'zod';
import { env } from '../config/env';
import { persistJobAndEnqueue } from '../services/workflow';
import { createUploadContext, ensureUploadContext, removeUploadContext } from '../services/document-storage';
import { updateJob } from '../services/job-store';
import type { JobRecord } from '../types/job';
import { asyncHandler } from '../utils/async-handler';

const notificationUrlSchema = z.string().url();

function toJobRecord(jobId: string, documentPath: string, notificationUrl?: string ): JobRecord {
  const now = new Date().toISOString();

  const job: JobRecord = {
    id: jobId,
    status: 'queued',
    createdAt: now,
    updatedAt: now,
    documentPath,
    attempts: 0
  };

  if (notificationUrl !== undefined) {
    job.notificationUrl = notificationUrl;
  }

  return job;
}

function prepareUploadContext(req: Request, _res: Response, next: NextFunction) {
  req.uploadContext = createUploadContext(randomUUID());
  next();
}

const storage = diskStorage({
  destination: (req, _file, cb) => {
    const context = req.uploadContext;

    if (!context) {
      cb(new Error('Missing upload context'), env.DOCUMENT_STORAGE_DIR);
      return;
    }

    ensureUploadContext(context.jobId)
      .then((nextContext) => {
        req.uploadContext = nextContext;
        cb(null, nextContext.jobDir);
      })
      .catch((error) => {
        cb(error as Error, context.jobDir);
      });
  },
  filename: (_req, _file, cb) => {
    cb(null, 'source.pdf');
  }
});

const upload = multer({
  storage,
  limits: {
    fileSize: 50 * 1024 * 1024
  }
});

function readNotificationUrl(value: unknown): string | undefined {
  if (typeof value !== 'string' || value.trim() === '') {
    return undefined;
  }

  const parsed = notificationUrlSchema.safeParse(value.trim());
  if (!parsed.success) {
    throw new Error('notificationUrl must be a valid URL');
  }

  return parsed.data;
}

export const extractRouter = Router();

extractRouter.post(
  '/',
  prepareUploadContext,
  upload.single('document'),
  asyncHandler(async (req: Request, res: Response) => {
    const context = req.uploadContext;

    if (!context) {
      return res.status(500).json({
        error: 'Upload context missing'
      });
    }

    if (!req.file) {
      await removeUploadContext(context.jobId);
      return res.status(400).json({
        error: 'A PDF document is required'
      });
    }

    let notificationUrl: string | undefined;

    try {
      notificationUrl = readNotificationUrl(req.body.notificationUrl);
    } catch (error) {
      await removeUploadContext(context.jobId);
      return res.status(400).json({
        error: error instanceof Error ? error.message : 'Invalid notificationUrl'
      });
    }

    const job = toJobRecord(context.jobId, context.documentPath, notificationUrl);

    try {
      await persistJobAndEnqueue(job);

      return res.status(202).json({
        jobId: job.id,
        status: job.status,
        statusUrl: `/jobs/${job.id}`,
        documentPath: job.documentPath,
        queue: env.BULLMQ_QUEUE_NAME
      });
    } catch (error) {
      await updateJob(job.id, {
        status: 'failed',
        error: error instanceof Error ? error.message : 'Unknown error',
        completedAt: new Date().toISOString()
      });
      await removeUploadContext(job.id);

      return res.status(500).json({
        error: 'Failed to start extraction workflow',
        details: error instanceof Error ? error.message : 'Unknown error'
      });
    }
  })
);
