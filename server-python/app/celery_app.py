"""
Celery instance shared by the API (producer) and the worker (consumer).

Job state is tracked in the Redis job store, not in a Celery result backend,
so `task_ignore_result` keeps the broker free of result keys.
"""

from celery import Celery

from app.config import settings

celery_app = Celery("pdf-extract", broker=settings.redis_url, include=["app.tasks"])

celery_app.conf.update(
    task_default_queue=settings.queue_name,
    task_ignore_result=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
