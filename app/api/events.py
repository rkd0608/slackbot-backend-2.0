"""Slack Events API webhook endpoints"""
from typing import Optional
import asyncio
import json

from fastapi import APIRouter, Request, HTTPException, Header, BackgroundTasks

from app.core.logging import get_logger
from app.services.slack_events import slack_event_handler

logger = get_logger(__name__)
router = APIRouter()


@router.post("/slack/events")
async def slack_events_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
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
    event_data = json.loads(body_bytes)

    # Handle URL verification synchronously (required by Slack)
    event_type = event_data.get("type")
    if event_type == "url_verification":
        return {"challenge": event_data.get("challenge")}

    # Process all other events asynchronously to respond to Slack within 3 seconds
    # Fire and forget - don't await
    asyncio.create_task(slack_event_handler.handle_event(event_data))

    # Return immediately to Slack
    return {"status": "ok"}
