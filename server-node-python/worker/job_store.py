import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from redis import Redis


def job_key(prefix: str, job_id: str) -> str:
    return f"{prefix}{job_id}"


def load_job(redis_client: Redis, prefix: str, job_id: str) -> Optional[Dict[str, Any]]:
    raw = redis_client.get(job_key(prefix, job_id))
    if raw is None:
        return None
    return json.loads(raw)


def save_job(redis_client: Redis, prefix: str, job: Dict[str, Any], ttl_seconds: int) -> None:
    redis_client.set(job_key(prefix, job["id"]), json.dumps(job), ex=ttl_seconds)


def update_job(
    redis_client: Redis,
    prefix: str,
    job_id: str,
    patch: Dict[str, Any],
    ttl_seconds: int
) -> Optional[Dict[str, Any]]:
    current = load_job(redis_client, prefix, job_id)
    if current is None:
        return None

    current.update(patch)
    current["updatedAt"] = datetime.now(timezone.utc).isoformat()
    save_job(redis_client, prefix, current, ttl_seconds)
    return current
