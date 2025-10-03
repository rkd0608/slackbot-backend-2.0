"""Event consumer worker for processing Slack events"""
import asyncio
from typing import Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.workers.base_consumer import BaseConsumer
from app.services.message_processor import message_processor
from app.core.database import db_manager
from app.core.queue import QueueManager, queue_manager
from app.core.logging import get_logger
from app.core.monitoring import ingestion_rate, processing_rate, ingestion_lag
from app.models.message import Message
from app.models.thread import Thread
import time

logger = get_logger(__name__)


class EventConsumer(BaseConsumer):
    """Consumes and processes events from the events queue"""

    def __init__(self):
        super().__init__(QueueManager.EVENTS_QUEUE)

    def process_message(self, message: Dict[str, Any]) -> bool:
        """Process Slack event message"""
        event = message.get("event", {})
        event_type = event.get("type")
        team_id = message.get("team_id")

        # Track ingestion metrics
        ingestion_rate.labels(event_type=event_type).inc()

        # Calculate lag
        event_time = message.get("event_time", time.time())
        lag = time.time() - event_time
        ingestion_lag.observe(lag)

        logger.info(
            "processing_event",
            event_type=event_type,
            channel=event.get("channel"),
            user=event.get("user")
        )

        # Route to appropriate handler
        success = False
        try:
            if event_type == "message":
                success = asyncio.run(self._handle_message_event(event, team_id))
            elif event_type == "message_changed":
                success = asyncio.run(self._handle_message_changed(event, team_id))
            elif event_type == "message_deleted":
                success = asyncio.run(self._handle_message_deleted(event, team_id))
            elif event_type == "reaction_added":
                success = asyncio.run(self._handle_reaction_added(event, team_id))
            elif event_type == "reaction_removed":
                success = asyncio.run(self._handle_reaction_removed(event, team_id))
            elif event_type == "file_shared":
                success = asyncio.run(self._handle_file_shared(event, team_id))
            elif event_type == "channel_created":
                success = asyncio.run(self._handle_channel_created(event, team_id))
            elif event_type == "channel_rename":
                success = asyncio.run(self._handle_channel_rename(event, team_id))
            elif event_type == "member_joined_channel":
                success = asyncio.run(self._handle_member_joined(event, team_id))
            elif event_type == "member_left_channel":
                success = asyncio.run(self._handle_member_left(event, team_id))
            else:
                logger.warning("unknown_event_type", event_type=event_type)
                success = True  # Don't retry unknown events

            processing_rate.labels(status="success" if success else "failed").inc()
            return success

        except Exception as e:
            logger.error("event_processing_error", event_type=event_type, error=str(e))
            processing_rate.labels(status="error").inc()
            return False

    async def _handle_message_event(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle new message event"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                # Skip bot messages if needed
                if event.get("subtype") == "bot_message":
                    # Could process bot messages separately or skip
                    pass

                # Process the message
                message = await message_processor.process_message_event(event, team_id, db)

                # If this is a thread parent, create thread record
                if message and message.is_thread_parent:
                    await self._create_thread_record(message, db)

                return True
            except Exception as e:
                logger.error("handle_message_error", error=str(e))
                return False

    async def _handle_message_changed(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle message edit event"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                changed_message = event.get("message", {})
                await message_processor.process_message_event(changed_message, team_id, db)
                return True
            except Exception as e:
                logger.error("handle_message_changed_error", error=str(e))
                return False

    async def _handle_message_deleted(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle message deletion event"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                deleted_ts = event.get("deleted_ts")
                result = await db.execute(
                    select(Message).where(Message.message_id == deleted_ts)
                )
                message = result.scalar_one_or_none()

                if message:
                    # Soft delete or hard delete based on requirements
                    await db.delete(message)
                    await db.commit()
                    logger.info("message_deleted", message_id=deleted_ts)

                return True
            except Exception as e:
                logger.error("handle_message_deleted_error", error=str(e))
                return False

    async def _handle_reaction_added(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle reaction added to message"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                message_ts = event.get("item", {}).get("ts")
                reaction = event.get("reaction")

                result = await db.execute(
                    select(Message).where(Message.message_id == message_ts)
                )
                message = result.scalar_one_or_none()

                if message:
                    reactions = message.reactions or []
                    # Find existing reaction or add new
                    found = False
                    for r in reactions:
                        if r.get("name") == reaction:
                            r["count"] = r.get("count", 0) + 1
                            found = True
                            break

                    if not found:
                        reactions.append({"name": reaction, "count": 1})

                    message.reactions = reactions
                    message.reaction_count = sum(r.get("count", 0) for r in reactions)

                    # Recalculate importance
                    message.importance_score = message.importance_score + 0.5

                    await db.commit()
                    logger.info("reaction_added", message_id=message_ts, reaction=reaction)

                return True
            except Exception as e:
                logger.error("handle_reaction_added_error", error=str(e))
                return False

    async def _handle_reaction_removed(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle reaction removed from message"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                message_ts = event.get("item", {}).get("ts")
                reaction = event.get("reaction")

                result = await db.execute(
                    select(Message).where(Message.message_id == message_ts)
                )
                message = result.scalar_one_or_none()

                if message and message.reactions:
                    reactions = message.reactions
                    for r in reactions:
                        if r.get("name") == reaction:
                            r["count"] = max(0, r.get("count", 1) - 1)
                            break

                    # Remove reactions with 0 count
                    reactions = [r for r in reactions if r.get("count", 0) > 0]

                    message.reactions = reactions if reactions else None
                    message.reaction_count = sum(r.get("count", 0) for r in reactions)

                    await db.commit()

                return True
            except Exception as e:
                logger.error("handle_reaction_removed_error", error=str(e))
                return False

    async def _handle_file_shared(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle file shared event - queue for processing"""
        try:
            file_info = event.get("file", {})

            queue_manager.publish(
                queue=queue_manager.PROCESSING_QUEUE,
                message={
                    "type": "file_processing",
                    "file_id": file_info.get("id"),
                    "file_info": file_info,
                    "channel_id": event.get("channel_id"),
                    "user_id": event.get("user_id"),
                    "team_id": team_id
                }
            )

            logger.info("file_queued_for_processing", file_id=file_info.get("id"))
            return True
        except Exception as e:
            logger.error("handle_file_shared_error", error=str(e))
            return False

    async def _handle_channel_created(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle channel creation - queue for sync"""
        try:
            queue_manager.publish(
                queue=queue_manager.PROCESSING_QUEUE,
                message={
                    "type": "channel_sync",
                    "channel_id": event.get("channel", {}).get("id"),
                    "team_id": team_id
                }
            )
            return True
        except Exception as e:
            logger.error("handle_channel_created_error", error=str(e))
            return False

    async def _handle_channel_rename(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle channel rename - queue for sync"""
        return await self._handle_channel_created(event, team_id)

    async def _handle_member_joined(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle member joined channel"""
        try:
            queue_manager.publish(
                queue=queue_manager.PROCESSING_QUEUE,
                message={
                    "type": "member_sync",
                    "channel_id": event.get("channel"),
                    "user_id": event.get("user"),
                    "team_id": team_id,
                    "action": "joined"
                }
            )
            return True
        except Exception as e:
            logger.error("handle_member_joined_error", error=str(e))
            return False

    async def _handle_member_left(self, event: Dict[str, Any], team_id: str) -> bool:
        """Handle member left channel"""
        try:
            queue_manager.publish(
                queue=queue_manager.PROCESSING_QUEUE,
                message={
                    "type": "member_sync",
                    "channel_id": event.get("channel"),
                    "user_id": event.get("user"),
                    "team_id": team_id,
                    "action": "left"
                }
            )
            return True
        except Exception as e:
            logger.error("handle_member_left_error", error=str(e))
            return False

    async def _create_thread_record(self, message: Message, db: AsyncSession):
        """Create thread record for new thread"""
        try:
            thread = Thread(
                thread_ts=message.thread_ts,
                channel_id=message.channel_id,
                channel_name=message.channel_name,
                parent_message_id=message.id,
                parent_user_id=message.user_id,
                parent_text=message.text,
                reply_count=0,
                participant_count=1,
                participant_ids=[message.user_id],
                started_at=message.timestamp,
                importance_score=message.importance_score
            )

            db.add(thread)
            await db.commit()
            logger.info("thread_created", thread_ts=message.thread_ts)
        except Exception as e:
            logger.error("create_thread_error", error=str(e))


# Global event consumer instance
event_consumer = EventConsumer()
