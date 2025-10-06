"""Slack interactive components handler (button clicks, menus, etc.)"""
from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update
from app.core.database import get_db
from app.models.query_log import QueryLog
from app.core.logging import get_logger
from app.core.monitoring import feedback_responses
import json

router = APIRouter()
logger = get_logger(__name__)


@router.post("/slack/interactions")
async def handle_interaction(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Handle Slack interactive component callbacks (buttons, menus, etc.)"""
    try:
        # Slack sends interaction data as form data with a 'payload' field
        form_data = await request.form()
        payload_str = form_data.get("payload")

        if not payload_str:
            logger.warning("interaction_no_payload")
            return {"status": "error", "message": "No payload"}

        payload = json.loads(payload_str)

        # Log interaction for debugging
        logger.info(
            "interaction_received",
            type=payload.get("type"),
            user=payload.get("user", {}).get("id"),
            action=payload.get("actions", [{}])[0].get("action_id") if payload.get("actions") else None
        )

        # Handle block_actions (button clicks)
        if payload.get("type") == "block_actions":
            actions = payload.get("actions", [])

            for action in actions:
                action_id = action.get("action_id")

                # Handle feedback buttons
                if action_id in ["feedback_thumbs_up", "feedback_thumbs_down"]:
                    await handle_feedback_action(action, payload, db)

        # Slack expects a 200 OK response quickly
        return {"status": "ok"}

    except Exception as e:
        logger.error("interaction_handler_error", error=str(e))
        return {"status": "error", "message": str(e)}


async def handle_feedback_action(
    action: dict,
    payload: dict,
    db: AsyncSession
):
    """Handle feedback button clicks"""
    try:
        # Extract query_id from button value (format: "query_id:thumbs_up" or "query_id:thumbs_down")
        value = action.get("value", "")
        parts = value.split(":")

        if len(parts) != 2:
            logger.warning("invalid_feedback_value", value=value)
            return

        query_id = parts[0]
        feedback_type = parts[1]  # thumbs_up or thumbs_down

        # Map to rating (1 = thumbs down, 5 = thumbs up for binary feedback)
        rating = 5 if feedback_type == "thumbs_up" else 1

        # Update query log with feedback
        result = await db.execute(
            update(QueryLog)
            .where(QueryLog.query_id == query_id)
            .values(user_rating=rating)
        )
        await db.commit()

        if result.rowcount > 0:
            # Track feedback in Prometheus
            feedback_type = "thumbs_up" if feedback_type == "thumbs_up" else "thumbs_down"
            feedback_responses.labels(feedback_type=feedback_type).inc()

            logger.info(
                "feedback_recorded",
                query_id=query_id,
                rating=rating,
                user_id=payload.get("user", {}).get("id")
            )
        else:
            logger.warning("feedback_query_not_found", query_id=query_id)

    except Exception as e:
        logger.error("handle_feedback_error", error=str(e), query_id=query_id if 'query_id' in locals() else None)
        await db.rollback()
