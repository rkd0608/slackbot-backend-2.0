"""
Feedback API endpoints

Handles user feedback from Slack interactions (thumbs up/down buttons)
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import db_manager
from app.core.logging import get_logger
from app.services.feedback_loop_service import feedback_loop_service
from app.services.slack_client import slack_client_manager
import json
from typing import Dict, Any

logger = get_logger(__name__)

router = APIRouter()


@router.post("/slack/interactive")
async def handle_slack_interaction(request: Request):
    """
    Handle interactive Slack components (buttons, select menus, etc.)

    Slack sends payloads as form data with a 'payload' field containing JSON
    """
    try:
        # Parse form data
        form = await request.form()
        payload_str = form.get("payload")

        if not payload_str:
            raise HTTPException(status_code=400, detail="Missing payload")

        payload = json.loads(payload_str)

        logger.info(
            "slack_interaction_received",
            type=payload.get("type"),
            user=payload.get("user", {}).get("id"),
            team=payload.get("team", {}).get("id")
        )

        # Handle different interaction types
        interaction_type = payload.get("type")

        if interaction_type == "block_actions":
            # Button clicks, select menus, etc.
            return await handle_block_actions(payload)

        elif interaction_type == "view_submission":
            # Modal form submissions
            return await handle_view_submission(payload)

        elif interaction_type == "shortcut":
            # Global or message shortcuts
            return await handle_shortcut(payload)

        else:
            logger.warning("unknown_interaction_type", type=interaction_type)
            return {"response_type": "ephemeral", "text": "Unknown interaction type"}

    except json.JSONDecodeError as e:
        logger.error("payload_parse_error", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    except Exception as e:
        logger.error("interaction_handler_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


async def handle_block_actions(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle block action interactions (button clicks, etc.)"""
    try:
        user_id = payload.get("user", {}).get("id")
        team_id = payload.get("team", {}).get("id")
        actions = payload.get("actions", [])

        if not actions:
            return {"response_type": "ephemeral", "text": "No action found"}

        action = actions[0]  # Get first action
        action_id = action.get("action_id")
        value = action.get("value")

        logger.info(
            "block_action_processing",
            action_id=action_id,
            value=value,
            user=user_id
        )

        # Handle feedback buttons (thumbs up/down)
        if action_id and action_id.startswith("feedback_"):
            return await handle_feedback_action(
                action_id=action_id,
                value=value,
                user_id=user_id,
                team_id=team_id,
                payload=payload
            )

        # Handle other button types here
        # elif action_id == "show_more_results":
        #     return await handle_show_more(...)

        return {
            "response_type": "ephemeral",
            "text": "Action processed"
        }

    except Exception as e:
        logger.error("block_actions_error", error=str(e))
        return {
            "response_type": "ephemeral",
            "text": "Sorry, something went wrong processing your action."
        }


async def handle_feedback_action(
    action_id: str,
    value: str,
    user_id: str,
    team_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle thumbs up/down feedback buttons

    Value format: "{query_id}:thumbs_up" or "{query_id}:thumbs_down"
    """
    try:
        # Parse value
        parts = value.split(":")
        if len(parts) != 2:
            logger.error("invalid_feedback_value", value=value)
            return {
                "response_type": "ephemeral",
                "text": "Invalid feedback format"
            }

        query_id = parts[0]
        feedback_type = parts[1]  # 'thumbs_up' or 'thumbs_down'

        logger.info(
            "feedback_action_received",
            query_id=query_id,
            feedback_type=feedback_type,
            user=user_id
        )

        # Record feedback in database
        async for db in db_manager.get_session():
            success = await feedback_loop_service.record_feedback(
                query_id=query_id,
                user_id=user_id,
                team_id=team_id,
                feedback_type=feedback_type,
                context={
                    "source": "slack_button",
                    "timestamp": payload.get("message", {}).get("ts")
                },
                db=db
            )

            if not success:
                logger.error("feedback_recording_failed", query_id=query_id)
                return {
                    "response_type": "ephemeral",
                    "text": "Sorry, couldn't record your feedback. Please try again."
                }

            break  # Only use first session

        # Send acknowledgment to user
        feedback_emoji = "👍" if feedback_type == "thumbs_up" else "👎"
        thank_you_message = (
            f"{feedback_emoji} Thanks for your feedback! "
            "This helps me learn and improve my answers."
        )

        # Update the original message to show feedback was recorded
        # (Optional: could update the button styling or add a checkmark)

        logger.info(
            "feedback_recorded_successfully",
            query_id=query_id,
            feedback_type=feedback_type,
            team_id=team_id
        )

        return {
            "response_type": "ephemeral",
            "text": thank_you_message
        }

    except Exception as e:
        logger.error("feedback_action_error", error=str(e), query_id=query_id)
        return {
            "response_type": "ephemeral",
            "text": "Sorry, something went wrong. Your feedback wasn't recorded."
        }


async def handle_view_submission(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle modal form submissions"""
    try:
        # TODO: Implement if you add feedback modals
        logger.info("view_submission_received", payload=payload)
        return {}

    except Exception as e:
        logger.error("view_submission_error", error=str(e))
        return {
            "response_action": "errors",
            "errors": {"general": "Something went wrong"}
        }


async def handle_shortcut(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle global or message shortcuts"""
    try:
        # TODO: Implement if you add shortcuts (e.g., "Report issue" shortcut)
        logger.info("shortcut_received", callback_id=payload.get("callback_id"))
        return {}

    except Exception as e:
        logger.error("shortcut_error", error=str(e))
        return {}


@router.get("/feedback/analytics/{team_id}")
async def get_team_analytics(
    team_id: str,
    days: int = 7,
    db: AsyncSession = Depends(db_manager.get_session)
):
    """
    Get feedback analytics for a team

    This endpoint can be used by team admins to see how the bot is performing
    """
    try:
        analytics = await feedback_loop_service.analyze_team_performance(
            team_id=team_id,
            days=days,
            db=db
        )

        if not analytics:
            raise HTTPException(status_code=404, detail="No analytics found")

        return analytics

    except Exception as e:
        logger.error("analytics_endpoint_error", error=str(e), team_id=team_id)
        raise HTTPException(status_code=500, detail="Failed to fetch analytics")
