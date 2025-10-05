"""Slack API client and event handling"""
from typing import Optional, Dict, List, Any
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SlackClientManager:
    """Manages Slack API interactions"""

    def __init__(self):
        self.client: Optional[WebClient] = None

    def initialize(self):
        """Initialize Slack Web client"""
        self.client = WebClient(token=settings.slack_bot_token)

        # Test connection
        try:
            response = self.client.auth_test()
            logger.info(
                "slack_client_initialized",
                bot_id=response["bot_id"],
                team=response["team"]
            )
        except SlackApiError as e:
            logger.error("slack_init_error", error=str(e))
            raise

    async def get_channel_history(
        self,
        channel_id: str,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch channel message history"""
        try:
            response = self.client.conversations_history(
                channel=channel_id,
                limit=limit,
                cursor=cursor
            )
            return {
                "messages": response["messages"],
                "has_more": response.get("has_more", False),
                "next_cursor": response.get("response_metadata", {}).get("next_cursor")
            }
        except SlackApiError as e:
            logger.error("channel_history_error", channel_id=channel_id, error=str(e))
            return {"messages": [], "has_more": False, "next_cursor": None}

    async def get_thread_replies(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch thread replies"""
        try:
            response = self.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=limit
            )
            return response["messages"]
        except SlackApiError as e:
            logger.error("thread_replies_error", channel_id=channel_id, thread_ts=thread_ts, error=str(e))
            return []

    async def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch user information"""
        try:
            response = self.client.users_info(user=user_id)
            return response["user"]
        except SlackApiError as e:
            logger.error("user_info_error", user_id=user_id, error=str(e))
            return None

    async def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Fetch channel information"""
        try:
            response = self.client.conversations_info(channel=channel_id)
            return response["channel"]
        except SlackApiError as e:
            logger.error("channel_info_error", channel_id=channel_id, error=str(e))
            return None

    async def list_channels(self, types: str = "public_channel,private_channel") -> List[Dict[str, Any]]:
        """List all channels"""
        try:
            channels = []
            cursor = None

            while True:
                response = self.client.conversations_list(
                    types=types,
                    limit=200,
                    cursor=cursor
                )

                channels.extend(response["channels"])

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            logger.info("channels_listed", count=len(channels))
            return channels
        except SlackApiError as e:
            logger.error("list_channels_error", error=str(e))
            return []

    async def get_channel_members(self, channel_id: str) -> List[str]:
        """Get list of channel members"""
        try:
            members = []
            cursor = None

            while True:
                response = self.client.conversations_members(
                    channel=channel_id,
                    limit=200,
                    cursor=cursor
                )

                members.extend(response["members"])

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            return members
        except SlackApiError as e:
            logger.error("channel_members_error", channel_id=channel_id, error=str(e))
            return []

    async def download_file(self, url: str) -> Optional[bytes]:
        """Download file from Slack"""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {settings.slack_bot_token}"}
                )
                response.raise_for_status()
                return response.content
        except Exception as e:
            logger.error("file_download_error", url=url, error=str(e))
            return None

    async def post_message(
        self,
        channel: str,
        text: str = None,
        blocks: List[Dict[str, Any]] = None,
        thread_ts: str = None
    ) -> Optional[Dict[str, Any]]:
        """Post a message to a channel or thread"""
        try:
            response = self.client.chat_postMessage(
                channel=channel,
                text=text,
                blocks=blocks,
                thread_ts=thread_ts
            )
            logger.info(
                "message_posted",
                channel=channel,
                thread_ts=thread_ts,
                ts=response["ts"]
            )
            return response.data
        except SlackApiError as e:
            logger.error("post_message_error", channel=channel, error=str(e))
            return None

    async def post_ephemeral(
        self,
        channel: str,
        user: str,
        text: str = None,
        blocks: List[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Post an ephemeral message (only visible to specific user)"""
        try:
            response = self.client.chat_postEphemeral(
                channel=channel,
                user=user,
                text=text,
                blocks=blocks
            )
            logger.info("ephemeral_posted", channel=channel, user=user)
            return response.data
        except SlackApiError as e:
            logger.error("post_ephemeral_error", channel=channel, user=user, error=str(e))
            return None

    async def add_reaction(
        self,
        channel: str,
        timestamp: str,
        reaction: str
    ) -> bool:
        """Add a reaction to a message"""
        try:
            self.client.reactions_add(
                channel=channel,
                timestamp=timestamp,
                name=reaction
            )
            logger.info("reaction_added", channel=channel, ts=timestamp, reaction=reaction)
            return True
        except SlackApiError as e:
            logger.error("add_reaction_error", channel=channel, ts=timestamp, error=str(e))
            return False

    async def remove_reaction(
        self,
        channel: str,
        timestamp: str,
        reaction: str
    ) -> bool:
        """Remove a reaction from a message"""
        try:
            self.client.reactions_remove(
                channel=channel,
                timestamp=timestamp,
                name=reaction
            )
            logger.info("reaction_removed", channel=channel, ts=timestamp, reaction=reaction)
            return True
        except SlackApiError as e:
            logger.error("remove_reaction_error", channel=channel, ts=timestamp, error=str(e))
            return False

    async def update_message(
        self,
        channel: str,
        timestamp: str,
        text: str = None,
        blocks: List[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Update an existing message"""
        try:
            response = self.client.chat_update(
                channel=channel,
                ts=timestamp,
                text=text,
                blocks=blocks
            )
            logger.info("message_updated", channel=channel, ts=timestamp)
            return response.data
        except SlackApiError as e:
            logger.error("update_message_error", channel=channel, ts=timestamp, error=str(e))
            return None


# Global Slack client manager instance
slack_client_manager = SlackClientManager()
