import { JobRecord } from '../types/job';
import { saveJob } from './job-store';
import { enqueueExtractionJob } from './bullmq-queue';

export async function persistJobAndEnqueue(job: JobRecord) {
  await saveJob(job);
  await enqueueExtractionJob(job);
}
