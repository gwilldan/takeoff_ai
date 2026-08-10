from typing import Any, Dict

import requests

def send_notification(url: str, payload: Dict[str, Any]) -> None:
    if not url:
        print("No notification URL configured; skipping notification.")
        return

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            return
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                continue

    if last_error is not None:
        raise last_error
