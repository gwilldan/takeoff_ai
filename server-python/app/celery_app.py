"""
Celery instance shared by the API (producer) and the worker (consumer).

Redis is the broker. No task returns a result anyone waits on — pages publish
straight into the job record — so there is no result backend.
"""

from celery import Celery

from app.config import settings

celery_app = Celery("pdf-extract", broker=settings.redis_url, include=["app.tasks"])

celery_app.conf.update(
    task_default_queue=settings.queue_name,
    task_acks_late=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    # OCR of a dense scanned sheet can take ~1 GB; recycle a worker child that grows past 1.5 GB.
    worker_max_memory_per_child=1_500_000,  # KiB
    broker_connection_retry_on_startup=True,
)
