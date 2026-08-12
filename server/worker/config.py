from dataclasses import dataclass
import os

PDF_POINTS_PER_INCH = 72
MM_PER_INCH = 25.4
MIN_WALL_LENGTH_MM = 100
DEFAULT_SCALE_RATIO = 100


@dataclass(frozen=True)
class Settings:
    redis_url: str
    bullmq_queue_name: str
    redis_job_prefix: str
    job_ttl_seconds: int
    document_storage_dir: str
    notification_webhook_url: str


def load_settings() -> Settings:
    return Settings(
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379"),
        bullmq_queue_name=os.getenv("BULLMQ_QUEUE_NAME", "pdf-extract"),
        redis_job_prefix=os.getenv("REDIS_JOB_PREFIX", "pdf:extract:job:"),
        job_ttl_seconds=int(os.getenv("JOB_TTL_SECONDS", "604800")),
        document_storage_dir=os.getenv("DOCUMENT_STORAGE_DIR", "/data/uploads"),
        notification_webhook_url=os.getenv("NOTIFICATION_WEBHOOK_URL", ""),
    )
