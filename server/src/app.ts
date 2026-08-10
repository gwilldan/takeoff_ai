import cors from 'cors';
import express from 'express';
import helmet from 'helmet';
import { extractRouter } from './routes/extract';
import { jobsRouter } from './routes/jobs';

export function createApp(): express.Express {
  const app = express();

  app.disable('x-powered-by');
  app.use(helmet());
  app.use(cors());
  app.use(express.json({ limit: '1mb' }));

  app.get('/health', (_req, res) => {
    res.json({
      status: 'ok',
      service: 'pdf-extract-api',
      timestamp: new Date().toISOString()
    });
  });

  app.use('/extract', extractRouter);
  app.use('/jobs', jobsRouter);

  app.use((_req, res) => {
    res.status(404).json({ error: 'Route not found' });
  });

  app.use(
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    (err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
      const message = err instanceof Error ? err.message : 'Unexpected server error';
      res.status(500).json({ error: message });
    }
  );

  return app;
}
