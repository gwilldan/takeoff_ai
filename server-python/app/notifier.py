from typing import Any

import httpx


def send_notification(url: str, payload: dict[str, Any], attempts: int = 3) -> None:
    last_error: Exception | None = None

    for _ in range(attempts):
        try:
            httpx.post(url, json=payload, timeout=15).raise_for_status()
            return
        except Exception as exc:
            last_error = exc

    raise last_error  # type: ignore[misc]


def build_notification_payload(job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jobId": job["id"],
        "status": "completed",
        "source": {"documentPath": job.get("documentPath")},
        "completedAt": job.get("completedAt"),
        "result": result,
    }
