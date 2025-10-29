"""Synchronization service for channels, users, and members"""
from typing import Optional, List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models.channel import Channel
from app.models.user import User
from app.services.slack_client import slack_client_manager
from app.core.logging import get_logger
from app.core.queue import queue_manager

logger = get_logger(__name__)


class SyncService:
    """Synchronizes Slack workspace data with local database"""

    async def sync_all_channels(self, db: AsyncSession) -> int:
        """Sync all channels from Slack workspace"""
        try:
            channels = await slack_client_manager.list_channels()
            synced_count = 0

            for channel_data in channels:
                await self.sync_channel(channel_data["id"], db, channel_data)
                synced_count += 1

            logger.info("all_channels_synced", count=synced_count)
            return synced_count

        except Exception as e:
            logger.error("sync_all_channels_error", error=str(e))
            return 0

    async def sync_channel(
        self,
        channel_id: str,
        db: AsyncSession,
        channel_data: Optional[dict] = None
    ) -> Optional[Channel]:
        """Sync single channel metadata"""
        try:
            # Fetch channel data if not provided
            if not channel_data:
                channel_data = await slack_client_manager.get_channel_info(channel_id)

            if not channel_data:
                logger.error("channel_not_found", channel_id=channel_id)
                return None

            # Check if channel exists
            result = await db.execute(
                select(Channel).where(Channel.channel_id == channel_id)
            )
            existing = result.scalar_one_or_none()

            # Get channel members
            member_ids = await slack_client_manager.get_channel_members(channel_id)

            # Handle channel name - DMs don't have a "name" field
            channel_name = channel_data.get("name")
            if not channel_name:
                # DM channels (start with 'D') don't have names - generate synthetic name
                if channel_id.startswith("D"):
                    # Use user IDs from members to create a readable DM identifier
                    if member_ids and len(member_ids) > 0:
                        channel_name = f"DM-{'-'.join(sorted(member_ids[:2]))}"  # Take first 2 users
                    else:
                        channel_name = f"DM-{channel_id}"
                    logger.info("dm_channel_name_generated", channel_id=channel_id, channel_name=channel_name)
                else:
                    # Fallback for other channel types without names
                    channel_name = f"Channel-{channel_id}"
                    logger.warning("channel_name_missing_using_fallback", channel_id=channel_id, channel_name=channel_name)

            if existing:
                # Update existing
                existing.channel_name = channel_name
                existing.is_private = 1 if channel_data.get("is_private") else 0
                existing.is_archived = 1 if channel_data.get("is_archived") else 0
                existing.topic = channel_data.get("topic", {}).get("value")
                existing.purpose = channel_data.get("purpose", {}).get("value")
                existing.member_ids = member_ids
                existing.member_count = len(member_ids)
                existing.last_synced_at = datetime.utcnow()

                channel = existing
            else:
                # Create new
                channel = Channel(
                    channel_id=channel_id,
                    channel_name=channel_name,
                    team_id=channel_data.get("context_team_id") or "unknown",
                    is_private=1 if channel_data.get("is_private") else 0,
                    is_archived=1 if channel_data.get("is_archived") else 0,
                    topic=channel_data.get("topic", {}).get("value"),
                    purpose=channel_data.get("purpose", {}).get("value"),
                    member_ids=member_ids,
                    member_count=len(member_ids),
                    slack_created_at=datetime.fromtimestamp(channel_data.get("created", 0)),
                    last_synced_at=datetime.utcnow()
                )
                db.add(channel)

            await db.commit()
            await db.refresh(channel)

            logger.info("channel_synced", channel_id=channel_id, channel_name=channel.channel_name)
            return channel

        except Exception as e:
            logger.error("sync_channel_error", channel_id=channel_id, error=str(e))
            await db.rollback()
            return None

    async def sync_channel_members(self, channel_id: str, db: AsyncSession) -> bool:
        """Sync channel member list"""
        try:
            member_ids = await slack_client_manager.get_channel_members(channel_id)

            result = await db.execute(
                select(Channel).where(Channel.channel_id == channel_id)
            )
            channel = result.scalar_one_or_none()

            if channel:
                channel.member_ids = member_ids
                channel.member_count = len(member_ids)
                channel.last_synced_at = datetime.utcnow()
                await db.commit()

                logger.info("channel_members_synced", channel_id=channel_id, count=len(member_ids))
                return True

            return False

        except Exception as e:
            logger.error("sync_channel_members_error", channel_id=channel_id, error=str(e))
            return False

    async def sync_user(self, user_id: str, db: AsyncSession) -> Optional[User]:
        """Sync single user profile"""
        try:
            user_data = await slack_client_manager.get_user_info(user_id)

            if not user_data:
                # Log as warning - system will use user_id instead of full name
                logger.warning("user_info_unavailable", user_id=user_id, reason="Could not fetch user details from Slack API")
                return None

            # Check if user exists
            result = await db.execute(
                select(User).where(User.user_id == user_id)
            )
            existing = result.scalar_one_or_none()

            profile = user_data.get("profile", {})

            if existing:
                # Update existing
                existing.username = user_data.get("name")
                existing.real_name = user_data.get("real_name")
                existing.display_name = profile.get("display_name")
                existing.email = profile.get("email")
                existing.is_admin = 1 if user_data.get("is_admin") else 0
                existing.is_owner = 1 if user_data.get("is_owner") else 0
                existing.is_deleted = 1 if user_data.get("deleted") else 0
                existing.title = profile.get("title")
                existing.timezone = user_data.get("tz")
                existing.avatar_url = profile.get("image_512")
                existing.last_active_at = datetime.utcnow()

                user = existing
            else:
                # Create new
                user = User(
                    user_id=user_id,
                    team_id=user_data.get("team_id", "unknown"),
                    username=user_data.get("name"),
                    real_name=user_data.get("real_name"),
                    display_name=profile.get("display_name"),
                    email=profile.get("email"),
                    is_bot=1 if user_data.get("is_bot") else 0,
                    is_admin=1 if user_data.get("is_admin") else 0,
                    is_owner=1 if user_data.get("is_owner") else 0,
                    is_deleted=1 if user_data.get("deleted") else 0,
                    title=profile.get("title"),
                    timezone=user_data.get("tz"),
                    avatar_url=profile.get("image_512"),
                    slack_created_at=datetime.fromtimestamp(user_data.get("updated", 0))
                )
                db.add(user)

            await db.commit()
            await db.refresh(user)

            logger.info("user_synced", user_id=user_id, username=user.username)
            return user

        except Exception as e:
            logger.error("sync_user_error", user_id=user_id, error=str(e))
            return None

    async def sync_all_users(self, db: AsyncSession) -> int:
        """Sync all users from Slack workspace"""
        try:
            # Get all unique user IDs from messages
            from sqlalchemy import distinct
            from app.models.message import Message

            result = await db.execute(
                select(distinct(Message.user_id))
            )
            user_ids = [row[0] for row in result.all()]

            synced_count = 0
            for user_id in user_ids:
                if await self.sync_user(user_id, db):
                    synced_count += 1

            logger.info("all_users_synced", count=synced_count)
            return synced_count

        except Exception as e:
            logger.error("sync_all_users_error", error=str(e))
            return 0


# Global sync service instance
sync_service = SyncService()
