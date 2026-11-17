"""
Celery instance shared by the API (producer) and the worker (consumer).

Redis is both broker and result backend. The result backend is required for
the per-page chord: Celery counts finished page tasks there to know when to
run the finalize step. Job state the frontend reads still lives in the job
store, so results expire quickly.
"""

from celery import Celery

from app.config import settings

celery_app = Celery("pdf-extract", broker=settings.redis_url, backend=settings.redis_url, include=["app.tasks"])

celery_app.conf.update(
    task_default_queue=settings.queue_name,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    # A page of an A1 sheet can take ~1.5 GB; recycle a worker child that grows past it.
    worker_max_memory_per_child=1_500_000,  # KiB
    broker_connection_retry_on_startup=True,
)
