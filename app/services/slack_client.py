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
            # Log as warning since this is non-critical (system continues with user_id)
            logger.warning("user_info_fetch_failed", user_id=user_id, error=str(e))
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

    async def get_file_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Fetch file information from Slack API"""
        try:
            response = self.client.files_info(file=file_id)
            file_data = response["file"]

            # Try to get a public URL using files.sharedPublicURL
            # This generates a time-limited public URL that doesn't require authentication
            try:
                public_response = self.client.files_sharedPublicURL(file=file_id)
                if public_response["ok"]:
                    file_data["permalink_public"] = public_response["file"].get("permalink_public")
                    logger.info(
                        "generated_public_url",
                        file_id=file_id,
                        has_public_url=bool(public_response["file"].get("permalink_public"))
                    )
            except SlackApiError as e:
                # Not all files can have public URLs - this is non-critical
                logger.debug("public_url_not_available", file_id=file_id, error=str(e))

            return file_data
        except SlackApiError as e:
            logger.error("file_info_error", file_id=file_id, error=str(e))
            return None

    async def download_file(self, url: str, bot_token: Optional[str] = None) -> Optional[bytes]:
        """
        Download file from Slack.

        Args:
            url: The file URL to download
            bot_token: Optional workspace-specific bot token (for multi-tenancy)

        NOTE: Public permalinks don't require authentication.
        Private URLs require bot token but may still have auth issues.
        """
        try:
            import httpx

            # Use provided token or fall back to default
            token = bot_token or settings.slack_bot_token

            # Check if this is a public permalink (doesn't need auth)
            is_public = "slack.com/files-pri-pub/" in url or "/public/" in url

            logger.info(
                "attempting_file_download",
                url=url[:150],
                is_public_url=is_public,
                has_bot_token=bool(token),
                using_workspace_token=bool(bot_token)
            )

            # Build headers - only add auth for private URLs
            headers = {}
            if not is_public:
                headers["Authorization"] = f"Bearer {token}"

            # Follow all redirects automatically
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                response = await client.get(url, headers=headers)

                logger.info(
                    "download_response",
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type", ""),
                    final_url=str(response.url)[:150]
                )

                response.raise_for_status()

                # Validate that we got actual file content, not HTML
                content = response.content
                content_type = response.headers.get("content-type", "").lower()

                # Check if we accidentally got an HTML page instead of the file
                # Only reject if:
                # 1. Content looks like HTML
                # 2. BUT the URL doesn't indicate it should be an HTML file
                is_html_content = content.startswith(b'<!DOCTYPE') or content.startswith(b'<!doctype') or content.startswith(b'<html')
                is_html_file = url.lower().endswith('.html') or url.lower().endswith('.htm')

                if is_html_content and not is_html_file:
                    logger.error(
                        "received_html_instead_of_file",
                        url=url[:100],
                        content_type=content_type,
                        content_preview=content[:100]
                    )
                    return None

                logger.info(
                    "file_downloaded_successfully",
                    url=url[:100],
                    content_type=content_type,
                    size_bytes=len(content)
                )

                return content

        except Exception as e:
            logger.error("file_download_error", url=url[:100], error=str(e))
            return None

    async def post_message(
        self,
        channel: str,
        text: str = None,
        blocks: List[Dict[str, Any]] = None,
        thread_ts: str = None,
        bot_token: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Post a message to a channel or thread

        Args:
            channel: Channel ID
            text: Message text
            blocks: Message blocks
            thread_ts: Thread timestamp
            bot_token: Optional workspace-specific bot token (for multi-tenancy)
        """
        try:
            # Use workspace-specific token if provided, otherwise use global client
            client = WebClient(token=bot_token) if bot_token else self.client

            response = client.chat_postMessage(
                channel=channel,
                text=text,
                blocks=blocks,
                thread_ts=thread_ts
            )
            logger.info(
                "message_posted",
                channel=channel,
                thread_ts=thread_ts,
                ts=response["ts"],
                using_workspace_token=bool(bot_token)
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
        reaction: str,
        bot_token: Optional[str] = None
    ) -> bool:
        """Add a reaction to a message

        Args:
            channel: Channel ID
            timestamp: Message timestamp
            reaction: Reaction name (without colons)
            bot_token: Optional workspace-specific bot token (for multi-tenancy)
        """
        try:
            # Use workspace-specific token if provided, otherwise use global client
            client = WebClient(token=bot_token) if bot_token else self.client

            client.reactions_add(
                channel=channel,
                timestamp=timestamp,
                name=reaction
            )
            logger.info("reaction_added", channel=channel, ts=timestamp, reaction=reaction, using_workspace_token=bool(bot_token))
            return True
        except SlackApiError as e:
            logger.error("add_reaction_error", channel=channel, ts=timestamp, error=str(e))
            return False

    async def remove_reaction(
        self,
        channel: str,
        timestamp: str,
        reaction: str,
        bot_token: Optional[str] = None
    ) -> bool:
        """Remove a reaction from a message

        Args:
            channel: Channel ID
            timestamp: Message timestamp
            reaction: Reaction name (without colons)
            bot_token: Optional workspace-specific bot token (for multi-tenancy)
        """
        try:
            # Use workspace-specific token if provided, otherwise use global client
            client = WebClient(token=bot_token) if bot_token else self.client

            client.reactions_remove(
                channel=channel,
                timestamp=timestamp,
                name=reaction
            )
            logger.info("reaction_removed", channel=channel, ts=timestamp, reaction=reaction, using_workspace_token=bool(bot_token))
            return True
        except SlackApiError as e:
            logger.error("remove_reaction_error", channel=channel, ts=timestamp, error=str(e))
            return False

    async def update_message(
        self,
        channel: str,
        timestamp: str,
        text: str = None,
        blocks: List[Dict[str, Any]] = None,
        bot_token: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update an existing message

        Args:
            channel: Channel ID
            timestamp: Message timestamp
            text: New message text
            blocks: New message blocks
            bot_token: Optional workspace-specific bot token (for multi-tenancy)
        """
        try:
            # Use workspace-specific token if provided, otherwise use global client
            client = WebClient(token=bot_token) if bot_token else self.client

            response = client.chat_update(
                channel=channel,
                ts=timestamp,
                text=text,
                blocks=blocks
            )
            logger.info("message_updated", channel=channel, ts=timestamp, using_workspace_token=bool(bot_token))
            return response.data
        except SlackApiError as e:
            logger.error("update_message_error", channel=channel, ts=timestamp, error=str(e))
            return None


# Global Slack client manager instance
slack_client_manager = SlackClientManager()
