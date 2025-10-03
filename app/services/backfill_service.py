"""Historical data backfill service"""
import asyncio
from datetime import datetime
from typing import Optional, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.core.logging import get_logger
from app.core.monitoring import ingestion_rate
from app.models.channel import Channel
from app.services.message_processor import message_processor
from app.services.slack_client import slack_client_manager

logger = get_logger(__name__)


class BackfillService:
    """Handles historical data backfill from Slack"""

    CHECKPOINT_PREFIX = "backfill:checkpoint:"
    RATE_LIMIT_DELAY = 1.2  # Seconds between API calls (50 calls/min)

    async def backfill_channel(
        self,
        channel_id: str,
        db: AsyncSession,
        oldest_ts: Optional[str] = None,
        limit_messages: Optional[int] = None
    ) -> int:
        """Backfill messages from a single channel"""

        logger.info("backfill_started", channel_id=channel_id)

        # Get checkpoint if exists
        checkpoint_key = f"{self.CHECKPOINT_PREFIX}{channel_id}"
        checkpoint = await cache_manager.get(checkpoint_key)

        cursor = checkpoint.get("cursor") if checkpoint else None
        messages_processed = checkpoint.get("count", 0) if checkpoint else 0

        total_processed = 0

        try:
            while True:
                # Rate limiting
                await asyncio.sleep(self.RATE_LIMIT_DELAY)

                # Fetch history
                history = await slack_client_manager.get_channel_history(
                    channel_id=channel_id,
                    limit=100,
                    cursor=cursor
                )

                messages = history.get("messages", [])

                if not messages:
                    break

                # Process messages in batch
                for msg in messages:
                    # Check if we've reached the oldest timestamp
                    if oldest_ts and msg.get("ts") < oldest_ts:
                        logger.info("backfill_reached_oldest", channel_id=channel_id)
                        break

                    # Get team_id from channel info
                    channel_result = await db.execute(
                        select(Channel).where(Channel.channel_id == channel_id)
                    )
                    channel = channel_result.scalar_one_or_none()
                    team_id = channel.team_id if channel else "unknown"

                    # Ensure required fields present for processing
                    # Slack history messages may omit 'channel'; inject context
                    if not msg.get("channel"):
                        msg["channel"] = channel_id
                    if not msg.get("channel_name") and channel:
                        msg["channel_name"] = channel.channel_name

                    # Process message
                    await message_processor.process_message_event(msg, team_id, db)

                    total_processed += 1
                    messages_processed += 1

                    ingestion_rate.labels(event_type="backfill_message").inc()

                    # Check limit
                    if limit_messages and total_processed >= limit_messages:
                        logger.info("backfill_limit_reached", channel_id=channel_id, count=total_processed)
                        break

                # Save checkpoint
                if history.get("has_more"):
                    cursor = history.get("next_cursor")
                    await cache_manager.set(
                        checkpoint_key,
                        {"cursor": cursor, "count": messages_processed},
                        ttl=86400  # 24 hours
                    )
                else:
                    # Backfill complete
                    await cache_manager.delete(checkpoint_key)
                    break

                if limit_messages and total_processed >= limit_messages:
                    break

            logger.info("backfill_completed", channel_id=channel_id, total=total_processed)
            return total_processed

        except Exception as e:
            logger.error("backfill_error", channel_id=channel_id, error=str(e))
            # Save checkpoint for retry
            if cursor:
                await cache_manager.set(
                    checkpoint_key,
                    {"cursor": cursor, "count": messages_processed},
                    ttl=86400
                )
            return total_processed

    async def backfill_all_channels(
        self,
        db: AsyncSession,
        limit_per_channel: Optional[int] = 1000
    ) -> Dict[str, int]:
        """Backfill all channels prioritized by activity"""

        logger.info("backfill_all_started")

        # Get all channels
        result = await db.execute(select(Channel).where(Channel.is_archived == 0))
        channels = result.scalars().all()

        # Sort by message count (most active first)
        channels_sorted = sorted(
            channels,
            key=lambda c: c.total_messages,
            reverse=True
        )

        results = {}

        for channel in channels_sorted:
            count = await self.backfill_channel(
                channel.channel_id,
                db,
                limit_messages=limit_per_channel
            )
            results[channel.channel_id] = count

            # Update channel stats
            channel.total_messages += count
            channel.last_synced_at = datetime.utcnow()

        await db.commit()

        logger.info("backfill_all_completed", channels=len(results))
        return results

    async def backfill_thread(
        self,
        channel_id: str,
        thread_ts: str,
        db: AsyncSession
    ) -> int:
        """Backfill all replies in a thread"""

        try:
            logger.info("backfill_thread_started", channel_id=channel_id, thread_ts=thread_ts)

            # Get thread replies
            replies = await slack_client_manager.get_thread_replies(
                channel_id=channel_id,
                thread_ts=thread_ts
            )

            # Get team_id
            channel_result = await db.execute(
                select(Channel).where(Channel.channel_id == channel_id)
            )
            channel = channel_result.scalar_one_or_none()
            team_id = channel.team_id if channel else "unknown"

            processed_count = 0

            for reply in replies:
                # Ensure required fields are present for replies
                if not reply.get("channel"):
                    reply["channel"] = channel_id
                if not reply.get("channel_name") and channel:
                    reply["channel_name"] = channel.channel_name

                await message_processor.process_message_event(reply, team_id, db)
                processed_count += 1

            logger.info("backfill_thread_completed", thread_ts=thread_ts, count=processed_count)
            return processed_count

        except Exception as e:
            logger.error("backfill_thread_error", thread_ts=thread_ts, error=str(e))
            return 0

    async def resume_backfill(self, db: AsyncSession) -> Dict[str, int]:
        """Resume interrupted backfills from checkpoints"""

        logger.info("resume_backfill_started")

        # Find all checkpoint keys
        # This is a simplified version - in production, use Redis SCAN
        results = {}

        # Get channels with checkpoints
        result = await db.execute(select(Channel))
        channels = result.scalars().all()

        for channel in channels:
            checkpoint_key = f"{self.CHECKPOINT_PREFIX}{channel.channel_id}"
            checkpoint = await cache_manager.get(checkpoint_key)

            if checkpoint:
                logger.info("resuming_backfill", channel_id=channel.channel_id)
                count = await self.backfill_channel(channel.channel_id, db)
                results[channel.channel_id] = count

        logger.info("resume_backfill_completed", channels=len(results))
        return results


# Global backfill service instance
backfill_service = BackfillService()
