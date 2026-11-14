import { Router } from 'express';
import { getJob } from '../services/job-store';
import { asyncHandler } from '../utils/async-handler';

export const jobsRouter = Router();

jobsRouter.get(
  '/:jobId',
  asyncHandler(async (req, res) => {
    const { jobId } = req.params;

    if (!jobId) {
      return res.status(400).json({ error: 'jobId is required' });
    }

    const job = await getJob(jobId);

    if (!job) {
      return res.status(404).json({ error: 'Job not found' });
    }

    return res.json(job);
  })
);
