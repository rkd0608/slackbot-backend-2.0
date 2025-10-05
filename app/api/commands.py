"""Slack slash commands API endpoints"""
from fastapi import APIRouter, Request, Form, Depends, HTTPException, Header
from fastapi.responses import Response
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.bot_interaction import bot_interaction_service
from app.services.response_formatter import response_formatter
from app.core.logging import get_logger
from app.core.config import settings
import hmac
import hashlib
import time

logger = get_logger(__name__)
router = APIRouter()


@router.post("/slack/commands/ask")
async def ask_command(
    text: str = Form(...),
    user_id: str = Form(...),
    channel_id: str = Form(...),
    response_url: str = Form(...),
    trigger_id: str = Form(None),
    x_slack_request_timestamp: Optional[str] = Header(None),
    x_slack_signature: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """Handle /ask slash command"""

    # Note: Signature verification is complex with Form data in FastAPI
    # In production, implement middleware for signature verification
    # For now, we'll log the headers for debugging

    query = text.strip()

    if not query:
        # Return help message if no query provided
        help_response = response_formatter.format_help_message("ask")
        return {
            "response_type": "ephemeral",
            "blocks": help_response["blocks"],
            "text": help_response["text"]
        }

    logger.info(
        "ask_command_received",
        user_id=user_id,
        channel_id=channel_id,
        query=query
    )

    # Start async processing
    # We'll use the response_url to post the actual response later
    # For now, return immediate acknowledgment

    # Import asyncio to run in background
    import asyncio

    async def process_ask():
        """Process ask query in background"""
        # We need to post to a thread, but slash commands don't have thread_ts
        # So we'll post a new message and use its ts as thread
        from app.services.slack_client import slack_client_manager
        from app.core.database import db_manager

        # Post initial message to create thread
        initial_msg = await slack_client_manager.post_message(
            channel=channel_id,
            text=f"Question: {query}"
        )

        if initial_msg:
            thread_ts = initial_msg.get("ts")

            # Create new DB session for background task
            async for session in db_manager.get_session():
                # Now handle the ask query in that thread
                await bot_interaction_service.handle_ask_query(
                    query=query,
                    user_id=user_id,
                    channel=channel_id,
                    thread_ts=thread_ts,
                    db=session
                )

    # Schedule background task
    asyncio.create_task(process_ask())

    # Return 200 OK with no content to acknowledge command without displaying message
    return Response(status_code=200)


@router.post("/slack/commands/find")
async def find_command(
    text: str = Form(...),
    user_id: str = Form(...),
    channel_id: str = Form(...),
    response_url: str = Form(...),
    trigger_id: str = Form(None),
    x_slack_request_timestamp: Optional[str] = Header(None),
    x_slack_signature: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """Handle /find slash command"""

    # Note: Signature verification is complex with Form data in FastAPI
    # In production, implement middleware for signature verification

    query = text.strip()

    if not query:
        # Return help message if no query provided
        help_response = response_formatter.format_help_message("find")
        return {
            "response_type": "ephemeral",
            "blocks": help_response["blocks"],
            "text": help_response["text"]
        }

    logger.info(
        "find_command_received",
        user_id=user_id,
        channel_id=channel_id,
        query=query
    )

    # Start async processing
    import asyncio

    async def process_find():
        """Process find query in background"""
        from app.services.slack_client import slack_client_manager
        from app.core.database import db_manager

        # Post initial message to create thread
        initial_msg = await slack_client_manager.post_message(
            channel=channel_id,
            text=f"Search: {query}"
        )

        if initial_msg:
            thread_ts = initial_msg.get("ts")

            # Create new DB session for background task
            async for session in db_manager.get_session():
                # Now handle the find query in that thread
                await bot_interaction_service.handle_find_query(
                    query=query,
                    user_id=user_id,
                    channel=channel_id,
                    thread_ts=thread_ts,
                    db=session
                )

    # Schedule background task
    asyncio.create_task(process_find())

    # Return 200 OK with no content to acknowledge command without displaying message
    return Response(status_code=200)
