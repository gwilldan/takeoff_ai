import { connectRedis, closeRedis } from './config/redis';
import { env } from './config/env';
import { createApp } from './app';
import { closeBullmqQueue } from './services/bullmq-queue';

async function main() {
  await connectRedis();

  const app = createApp();

  const server = app.listen(env.PORT, () => {
    console.log(`PDF extraction API listening on port ${env.PORT}`);
  });

  const shutdown = async () => {
    server.close();
    await closeBullmqQueue();
    await closeRedis();
    process.exit(0);
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

main().catch((error) => {
  console.error('Failed to start API', error);
  process.exit(1);
});
