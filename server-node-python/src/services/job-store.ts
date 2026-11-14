import { env } from '../config/env';
import { getRedisClient } from '../config/redis';
import { JobRecord } from '../types/job';

export function jobRedisKey(jobId: string): string {
  return `${env.REDIS_JOB_PREFIX}${jobId}`;
}

function ttl(): number {
  return env.JOB_TTL_SECONDS;
}

export async function saveJob(job: JobRecord): Promise<JobRecord> {
  const redis = getRedisClient();
  await redis.set(jobRedisKey(job.id), JSON.stringify(job), 'EX', ttl());
  return job;
}

export async function getJob(jobId: string): Promise<JobRecord | null> {
  const redis = getRedisClient();
  const raw = await redis.get(jobRedisKey(jobId));

  if (!raw) {
    return null;
  }

  return JSON.parse(raw) as JobRecord;
}

export async function updateJob(
  jobId: string,
  patch: Partial<Omit<JobRecord, 'id' | 'createdAt'>>
): Promise<JobRecord | null> {
  const existing = await getJob(jobId);

  if (!existing) {
    return null;
  }

  const updated: JobRecord = {
    ...existing,
    ...patch,
    updatedAt: new Date().toISOString()
  };

  await saveJob(updated);
  return updated;
}
