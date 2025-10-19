"""Slack slash commands API endpoints"""
from typing import Optional

from fastapi import APIRouter, Form, Depends, Header
from fastapi.responses import Response

from app.core.logging import get_logger
from app.core.slack_verification import verify_slack_signature
from app.services.bot_interaction import bot_interaction_service
from app.services.response_formatter import response_formatter

logger = get_logger(__name__)
router = APIRouter()


async def process_ask_command(query: str, user_id: str, team_id: str, channel_id: str):
    """Process ask query in background with validation"""
    from app.services.slack_client import slack_client_manager
    from app.core.database import db_manager
    from app.services.workspace_service import workspace_service

    try:
        # Do validation in background
        async for db in db_manager.get_session():
            validation = await workspace_service.validate_workspace_access(team_id, db)

            if not validation["valid"]:
                # Post error message to Slack
                await slack_client_manager.post_message(
                    channel=channel_id,
                    text=f"{validation['message']}"
                )
                break

            if not query:
                # Post help message
                help_response = response_formatter.format_help_message("ask")
                await slack_client_manager.post_message(
                    channel=channel_id,
                    text=help_response.get("text", "Please provide a query")
                )
                break

            # Post initial message to create thread
            initial_msg = await slack_client_manager.post_message(
                channel=channel_id,
                text=f"Question: {query}"
            )

            if initial_msg:
                thread_ts = initial_msg.get("ts")

                # Increment query usage
                await workspace_service.increment_query_usage(team_id, db)

                # Handle the ask query in that thread
                await bot_interaction_service.handle_ask_query(
                    query=query,
                    user_id=user_id,
                    channel=channel_id,
                    thread_ts=thread_ts,
                    db=db
                )
            break  # Only use first session

    except Exception as e:
        logger.error("background_ask_error", error=str(e), team_id=team_id)


@router.post("/slack/commands/ask")
async def ask_command(
    text: str = Form(...),
    user_id: str = Form(...),
    team_id: str = Form(...),
    channel_id: str = Form(...),
    response_url: str = Form(...),
    trigger_id: str = Form(None),
    x_slack_request_timestamp: Optional[str] = Header(None),
    x_slack_signature: Optional[str] = Header(None),
    verified: bool = Depends(verify_slack_signature)
):
    """Handle /ask slash command - spawns independent task"""
    import asyncio

    query = text.strip()

    logger.info(
        "ask_command_received",
        team_id=team_id,
        user_id=user_id,
        channel_id=channel_id,
        query=query
    )

    # Create truly independent background task (not tied to request)
    asyncio.create_task(
        process_ask_command(query, user_id, team_id, channel_id)
    )

    # Return 200 OK with empty content (don't show anything to user)
    return Response(status_code=200, content="")


async def process_find_command(query: str, user_id: str, team_id: str, channel_id: str):
    """Process find query in background"""
    from app.services.slack_client import slack_client_manager
    from app.core.database import db_manager

    try:
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
                break  # Only use first session
    except Exception as e:
        logger.error("background_find_error", error=str(e), team_id=team_id)


@router.post("/slack/commands/find")
async def find_command(
    text: str = Form(...),
    user_id: str = Form(...),
    team_id: str = Form(...),
    channel_id: str = Form(...),
    response_url: str = Form(...),
    trigger_id: str = Form(None),
    x_slack_request_timestamp: Optional[str] = Header(None),
    x_slack_signature: Optional[str] = Header(None),
    verified: bool = Depends(verify_slack_signature)
):
    """Handle /find slash command"""
    import asyncio

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

    # Create truly independent background task (not tied to request)
    asyncio.create_task(
        process_find_command(query, user_id, team_id, channel_id)
    )

    # Return 200 OK with empty content (don't show anything to user)
    return Response(status_code=200, content="")
