"""
Runtime settings, read once from the environment (and a local `.env` when present).

One object serves both the API and the Celery worker so the two processes can
never disagree about queue names, key prefixes or storage paths.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 4000
    redis_url: str = "redis://127.0.0.1:6379"
    redis_job_prefix: str = "pdf:extract:job:"
    job_ttl_seconds: int = 60 * 60 * 24 * 7
    document_storage_dir: str = "/data/uploads"
    max_upload_bytes: int = 50 * 1024 * 1024

    queue_name: str = "pdf-extract"
    queue_attempts: int = Field(default=3, ge=1)
    queue_backoff_delay_ms: int = Field(default=2000, ge=0)

    notification_webhook_url: str = ""

    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.7-plus"
    llm_vision_model: str | None = None

    extraction_cache_dir: str = "/tmp/takeoff-extraction-cache"
    extraction_output_dir: str = "/data/output"

    def model_post_init(self, __context) -> None:
        if self.llm_vision_model is None:
            self.llm_vision_model = self.llm_model


settings = Settings()
