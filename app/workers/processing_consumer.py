"""Processing consumer for files, channel sync, user sync"""
import asyncio
from typing import Dict, Any
from app.workers.base_consumer import BaseConsumer
from app.services.file_processor import file_processor
from app.core.database import db_manager
from app.core.queue import QueueManager
from app.core.logging import get_logger

logger = get_logger(__name__)


class ProcessingConsumer(BaseConsumer):
    """Consumes processing tasks (files, sync operations)"""

    def __init__(self):
        super().__init__(QueueManager.PROCESSING_QUEUE)

    def process_message(self, message: Dict[str, Any]) -> bool:
        """Route processing tasks to appropriate handlers"""
        task_type = message.get("type")

        logger.info("processing_task", task_type=task_type)

        try:
            # Use nest_asyncio to allow nested event loops
            import nest_asyncio
            nest_asyncio.apply()

            loop = asyncio.get_event_loop()

            if task_type == "file_processing":
                return loop.run_until_complete(self._process_file(message))
            elif task_type == "channel_sync":
                return loop.run_until_complete(self._sync_channel(message))
            elif task_type == "channel_backfill":
                return loop.run_until_complete(self._backfill_channel(message))
            elif task_type == "channel_archive":
                return loop.run_until_complete(self._archive_channel(message))
            elif task_type == "member_sync":
                return loop.run_until_complete(self._sync_member(message))
            elif task_type == "user_sync":
                return loop.run_until_complete(self._sync_user(message))
            else:
                logger.warning("unknown_task_type", task_type=task_type)
                return True  # Don't retry unknown tasks

        except Exception as e:
            logger.error("processing_task_error", task_type=task_type, error=str(e))
            return False

    async def _process_file(self, message: Dict[str, Any]) -> bool:
        """Process file download and extraction"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                file_info = message.get("file_info", {})
                channel_id = message.get("channel_id")
                user_id = message.get("user_id")
                team_id = message.get("team_id")
                message_id = message.get("message_id")

                await file_processor.process_file(
                    file_info,
                    channel_id,
                    user_id,
                    team_id,
                    db,
                    message_id
                )

                return True
            except Exception as e:
                logger.error("file_processing_failed", error=str(e))
                return False

    async def _sync_channel(self, message: Dict[str, Any]) -> bool:
        """Sync channel metadata"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                from app.services.sync_service import sync_service
                channel_id = message.get("channel_id")
                await sync_service.sync_channel(channel_id, db)
                return True
            except Exception as e:
                logger.error("channel_sync_failed", error=str(e))
                return False

    async def _sync_member(self, message: Dict[str, Any]) -> bool:
        """Sync channel member"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                from app.services.sync_service import sync_service
                channel_id = message.get("channel_id")
                await sync_service.sync_channel_members(channel_id, db)
                return True
            except Exception as e:
                logger.error("member_sync_failed", error=str(e))
                return False

    async def _sync_user(self, message: Dict[str, Any]) -> bool:
        """Sync user profile"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                from app.services.sync_service import sync_service
                user_id = message.get("user_id")
                await sync_service.sync_user(user_id, db)
                return True
            except Exception as e:
                logger.error("user_sync_failed", error=str(e))
                return False

    async def _backfill_channel(self, message: Dict[str, Any]) -> bool:
        """Backfill historical messages from a channel"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                from app.services.backfill_service import backfill_service
                from app.core.queue import queue_manager

                channel_id = message.get("channel_id")
                team_id = message.get("team_id")

                logger.info("channel_backfill_started", channel_id=channel_id, team_id=team_id)

                # First, sync channel metadata to ensure it's in the database
                from app.services.sync_service import sync_service
                await sync_service.sync_channel(channel_id, db)

                # Backfill messages from the channel (limit to 1000 messages for new channels)
                message_count = await backfill_service.backfill_channel(
                    channel_id=channel_id,
                    db=db,
                    limit_messages=1000
                )

                logger.info(
                    "channel_backfill_completed",
                    channel_id=channel_id,
                    messages_backfilled=message_count
                )

                # Queue messages for embedding (they'll be picked up by embedding consumer)
                # The message processor already queues embeddings, so we're done

                return True
            except Exception as e:
                logger.error("channel_backfill_failed", channel_id=message.get("channel_id"), error=str(e))
                return False

    async def _archive_channel(self, message: Dict[str, Any]) -> bool:
        """Archive channel when bot is removed"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                from sqlalchemy import select, update
                from app.models.channel import Channel
                from datetime import datetime

                channel_id = message.get("channel_id")
                team_id = message.get("team_id")

                logger.info("channel_archive_started", channel_id=channel_id, team_id=team_id)

                # Mark channel as archived
                result = await db.execute(
                    select(Channel).where(Channel.channel_id == channel_id)
                )
                channel = result.scalar_one_or_none()

                if channel:
                    channel.is_archived = 1  # MySQL boolean
                    channel.updated_at = datetime.utcnow()
                    await db.commit()

                    logger.info(
                        "channel_archived",
                        channel_id=channel_id,
                        channel_name=channel.channel_name,
                        total_messages=channel.total_messages
                    )
                else:
                    logger.warning("channel_not_found_for_archiving", channel_id=channel_id)

                # Note: We keep messages and embeddings for historical purposes
                # They won't show up in searches because retrieval filters archived channels
                # Embeddings remain in Pinecone but won't be returned

                return True
            except Exception as e:
                logger.error("channel_archive_failed", channel_id=message.get("channel_id"), error=str(e))
                return False


# Global processing consumer instance
processing_consumer = ProcessingConsumer()
