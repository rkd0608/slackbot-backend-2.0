"""Slack Events API webhook handler"""
import hashlib
import hmac
import time
from typing import Dict, Any
from fastapi import Request, HTTPException
from app.core.config import settings
from app.core.logging import get_logger
from app.core.queue import queue_manager

logger = get_logger(__name__)


class SlackEventHandler:
    """Handles Slack Events API webhooks"""

    def __init__(self):
        self.signing_secret = settings.slack_signing_secret.encode()

    def verify_signature(self, request_body: bytes, timestamp: str, signature: str) -> bool:
        """Verify Slack request signature"""
        # Check timestamp to prevent replay attacks
        current_time = int(time.time())
        if abs(current_time - int(timestamp)) > 60 * 5:  # 5 minutes
            logger.warning("slack_signature_timestamp_invalid", timestamp=timestamp)
            return False

        # Verify signature
        sig_basestring = f"v0:{timestamp}:{request_body.decode()}".encode()
        my_signature = 'v0=' + hmac.new(
            self.signing_secret,
            sig_basestring,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(my_signature, signature)

    async def handle_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming Slack event"""
        event_type = event_data.get("type")

        # URL verification challenge
        if event_type == "url_verification":
            return {"challenge": event_data.get("challenge")}

        # Handle installation/uninstallation events
        if event_type == "app_uninstalled":
            team_id = event_data.get("team_id")
            if not team_id:
                logger.error("app_uninstalled_missing_team_id")
                return {"status": "error", "message": "Missing team_id"}

            logger.info("app_uninstalled", team_id=team_id)

            # Queue uninstallation handler
            await queue_manager.publish(
                queue=queue_manager.EVENTS_QUEUE,
                message={
                    "event_type": "app_uninstalled",
                    "team_id": team_id,
                    "event_time": event_data.get("event_time")
                },
                routing_key="slack.event.app_uninstalled"
            )
            return {"status": "ok"}

        if event_type == "tokens_revoked":
            team_id = event_data.get("team_id")
            if not team_id:
                logger.error("tokens_revoked_missing_team_id")
                return {"status": "error", "message": "Missing team_id"}

            logger.info("tokens_revoked", team_id=team_id)

            # Queue token revocation handler
            await queue_manager.publish(
                queue=queue_manager.EVENTS_QUEUE,
                message={
                    "event_type": "tokens_revoked",
                    "team_id": team_id,
                    "tokens": event_data.get("tokens", {}),
                    "event_time": event_data.get("event_time")
                },
                routing_key="slack.event.tokens_revoked"
            )
            return {"status": "ok"}

        # Event callback
        if event_type == "event_callback":
            event = event_data.get("event", {})
            event_subtype = event.get("type")
            team_id = event_data.get("team_id")

            logger.info(
                "slack_event_received",
                event_type=event_subtype,
                team_id=team_id,
                channel=event.get("channel"),
                user=event.get("user")
            )

            # Route event to appropriate queue with team_id for workspace isolation
            routing_key = f"slack.event.{event_subtype}"
            await queue_manager.publish(
                queue=queue_manager.EVENTS_QUEUE,
                message={
                    "event": event,
                    "team_id": team_id,
                    "event_id": event_data.get("event_id"),
                    "event_time": event_data.get("event_time")
                },
                routing_key=routing_key
            )

            return {"status": "ok"}

        logger.warning("unknown_event_type", event_type=event_type)
        return {"status": "unknown_event_type"}


# Global event handler instance
slack_event_handler = SlackEventHandler()
