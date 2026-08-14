'use client';

import { useCallback, useEffect, useState } from 'react';
import { fetchJob, startExtraction, type JobRecord } from '../../lib/api';

export const POLL_INTERVAL_MS = 2500;

export type ExtractionJobState = {
  job: JobRecord | null;
  message: string;
  isRunning: boolean;
  start: (file: File, notificationUrl?: string) => Promise<void>;
  clear: () => void;
};

function isTerminal(job: JobRecord | null): boolean {
  return job === null || job.status === 'completed' || job.status === 'failed';
}

export function useExtractionJob(): ExtractionJobState {
  const [job, setJob] = useState<JobRecord | null>(null);
  const [message, setMessage] = useState('');
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (!job || isTerminal(job)) {
      return;
    }

    const timer = window.setInterval(async () => {
      try {
        setJob(await fetchJob(job.id));
      } catch {
        // Keep polling quietly — the API may still be coming up.
      }
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(timer);
  }, [job]);

  const start = useCallback(async (file: File, notificationUrl?: string) => {
    setStarting(true);
    setMessage('');

    try {
      const response = await startExtraction(file, notificationUrl);
      const now = new Date().toISOString();

      setJob({
        id: response.jobId,
        status: response.status,
        createdAt: now,
        updatedAt: now,
        documentPath: response.documentPath,
        attempts: 0,
        ...(notificationUrl ? { notificationUrl } : {})
      });
      setMessage(`Job ${response.jobId} queued on ${response.queue}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unexpected error');
    } finally {
      setStarting(false);
    }
  }, []);

  const clear = useCallback(() => {
    setJob(null);
    setMessage('');
  }, []);

  return { job, message, isRunning: starting || !isTerminal(job), start, clear };
}
