import Redis from 'ioredis';
import { env } from './env';

let client: Redis | null = null;

export function getRedisClient(): Redis {
  if (!client) {
    client = new Redis(env.REDIS_URL, {
      lazyConnect: true,
      maxRetriesPerRequest: null,
      enableReadyCheck: true
    });
  }

  return client;
}

export async function connectRedis() {
  const redis = getRedisClient();

  if (redis.status === 'wait' || redis.status === 'end') {
    await redis.connect();
  }

  await redis.ping();
  return redis;
}

export async function closeRedis() {
  if (!client) {
    return;
  }

  await client.quit();
  client = null;
}
