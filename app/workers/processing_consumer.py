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

                await file_processor.process_file(
                    file_info,
                    channel_id,
                    user_id,
                    team_id,
                    db
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


# Global processing consumer instance
processing_consumer = ProcessingConsumer()
