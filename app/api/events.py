"""Slack Events API webhook endpoints"""
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Header

from app.core.logging import get_logger
from app.services.slack_events import slack_event_handler

logger = get_logger(__name__)
router = APIRouter()


@router.post("/slack/events")
async def slack_events_webhook(
    request: Request,
    x_slack_request_timestamp: Optional[str] = Header(None),
    x_slack_signature: Optional[str] = Header(None)
):
    """Webhook endpoint for Slack Events API"""

    # Get raw request body ONCE (calling both body() and json() causes Starlette errors)
    body_bytes = await request.body()

    # Verify Slack signature
    if x_slack_request_timestamp and x_slack_signature:
        is_valid = slack_event_handler.verify_signature(
            body_bytes,
            x_slack_request_timestamp,
            x_slack_signature
        )

        if not is_valid:
            logger.warning("slack_signature_verification_failed")
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        logger.warning("slack_signature_headers_missing")

    # Parse JSON from bytes (don't call request.json() after request.body())
    import json
    event_data = json.loads(body_bytes)

    # Process event using asyncio (independent task)
    response = await slack_event_handler.handle_event(event_data)

    return response
