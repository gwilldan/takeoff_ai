import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update(
    DOCUMENT_STORAGE_DIR=f"{_tmp}/uploads",
    EXTRACTION_OUTPUT_DIR=f"{_tmp}/output",
    QUEUE_BACKOFF_DELAY_MS="0",
)

import fakeredis
import pytest

import app.redis_client as redis_client


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Swap the Redis class for an in-memory fake; every caller goes through get_redis()."""
    monkeypatch.setattr(redis_client, "Redis", fakeredis.FakeRedis)
    redis_client.get_redis.cache_clear()
    yield
    redis_client.get_redis.cache_clear()
