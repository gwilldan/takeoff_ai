"""
QuantTakeoff API client (v1.1).

A takeoff is started by posting a PDF as multipart form data; the response is
the takeoff's id, used to check its status and fetch its results. The form
takes no options — page choice, scale and units are ignored — and a takeoff
always measures the first page it is given, so each floor plan is sent as a
one-page PDF of its own.

Every call is authenticated with our key; a completion callback only tells us
to look, and the result is always fetched from the API itself, so a forged
callback can never inject data.
"""

from __future__ import annotations

from typing import Any

import fitz
import httpx

from app.config import settings

TERMINAL = {"succeeded", "failed", "canceled"}


class TakeoffApiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        # Their server is down or busy, not our request at fault: worth trying again.
        self.transient = status_code >= 500 or status_code == 429


def _client() -> httpx.Client:
    if not settings.quanttakeoff_api_key:
        raise TakeoffApiError("QUANTTAKEOFF_API_KEY is not set")
    return httpx.Client(
        base_url=settings.quanttakeoff_base_url,
        headers={"Authorization": f"Bearer {settings.quanttakeoff_api_key}"},
        timeout=120,
    )


def _json(response: httpx.Response) -> dict[str, Any]:
    if response.is_error:
        error = response.json().get("error", {}) if "json" in response.headers.get("content-type", "") else {}
        raise TakeoffApiError(
            f"QuantTakeoff {response.request.method} {response.request.url.path} returned {response.status_code}: "
            f"{error.get('message') or response.text[:200]} (request {error.get('request_id', 'unknown')})",
            response.status_code,
        )
    return response.json()


def single_page_pdf(pdf_path: str, page_index: int) -> bytes:
    """One page of the set as a PDF of its own; QuantTakeoff measures its first page."""
    with fitz.open(pdf_path) as source, fitz.open() as page:
        page.insert_pdf(source, from_page=page_index, to_page=page_index)
        return page.tobytes(garbage=3, deflate=True)


def create_takeoff(pdf_path: str, page_index: int, *, webhook_url: str | None, idempotency_key: str) -> str:
    """Start a takeoff of one page and return its id."""
    with _client() as client:
        return _json(client.post(
            "/takeoffs",
            headers={"Idempotency-Key": idempotency_key},
            files={"file": (f"page-{page_index + 1}.pdf", single_page_pdf(pdf_path, page_index), "application/pdf")},
            data={"webhook_url": webhook_url} if webhook_url else None,
        ))["id"]


def get_takeoff(takeoff_id: str) -> dict[str, Any]:
    with _client() as client:
        return _json(client.get(f"/takeoffs/{takeoff_id}"))


def get_results(takeoff_id: str) -> dict[str, Any]:
    with _client() as client:
        return _json(client.get(f"/takeoffs/{takeoff_id}/results"))
