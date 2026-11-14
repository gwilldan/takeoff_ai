import 'dotenv/config';
import { z } from 'zod';

const envSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  PORT: z.coerce.number().int().positive().default(3000),
  REDIS_URL: z.string().min(1).default('redis://127.0.0.1:6379'),
  REDIS_JOB_PREFIX: z.string().min(1).default('pdf:extract:job:'),
  JOB_TTL_SECONDS: z.coerce.number().int().positive().default(60 * 60 * 24 * 7),
  DOCUMENT_STORAGE_DIR: z.string().min(1).default('/data/uploads'),
  BULLMQ_QUEUE_NAME: z.string().min(1).default('pdf-extract'),
  BULLMQ_ATTEMPTS: z.coerce.number().int().positive().default(3),
  BULLMQ_BACKOFF_DELAY_MS: z.coerce.number().int().nonnegative().default(2000)
});

export const env = envSchema.parse(process.env);
