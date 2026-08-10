import IORedis from 'ioredis';
import { Queue } from 'bullmq';
import { env } from '../config/env';
import { JobRecord } from '../types/job';

type ExtractionJobData = {
  jobId: string;
  documentPath: string;
  notificationUrl?: string;
};

let connection: IORedis | null = null;
let queue: Queue<ExtractionJobData> | null = null;

function getConnection(): IORedis {
  if (!connection) {
    connection = new IORedis(env.REDIS_URL, {
      lazyConnect: true,
      maxRetriesPerRequest: null
    });
  }

  return connection;
}

function getQueue(): Queue<ExtractionJobData> {
  if (!queue) {
    queue = new Queue<ExtractionJobData>(env.BULLMQ_QUEUE_NAME, {
      connection: getConnection(),
      defaultJobOptions: {
        attempts: env.BULLMQ_ATTEMPTS,
        backoff: {
          type: 'exponential',
          delay: env.BULLMQ_BACKOFF_DELAY_MS
        },
        removeOnComplete: true,
        removeOnFail: false
      }
    });
  }

  return queue;
}

export async function enqueueExtractionJob(job: JobRecord) {
  if (!job.documentPath) {
    throw new Error('Job is missing a document path');
  }

  const payload: ExtractionJobData = {
    jobId: job.id,
    documentPath: job.documentPath
  };

  if (job.notificationUrl !== undefined) {
    payload.notificationUrl = job.notificationUrl;
  }

  await getQueue().add(
    'extract-document',
    payload,
    {
      jobId: job.id
    }
  );
}

export async function closeBullmqQueue() {
  if (queue) {
    await queue.close();
    queue = null;
  }

  if (connection) {
    await connection.quit();
    connection = null;
  }
}
