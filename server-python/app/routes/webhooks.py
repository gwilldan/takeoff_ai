"""
QuantTakeoff's completion callback.

The payload is not trusted: it only names a takeoff, and only one we started
and are still waiting on is acted on. The result itself is then fetched from
QuantTakeoff with our key by `collect-takeoff`, the same task polling runs.
"""

from fastapi import APIRouter, Request, status

from app.jobs import get_pending_takeoff
from app.tasks import collect_takeoff

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/quanttakeoff", status_code=status.HTTP_202_ACCEPTED)
async def takeoff_callback(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    takeoff_id = body.get("takeoff_id") or body.get("id") or data.get("takeoff_id") or data.get("id")
    if isinstance(takeoff_id, str) and get_pending_takeoff(takeoff_id):
        collect_takeoff.delay(takeoff_id)
    return {}
