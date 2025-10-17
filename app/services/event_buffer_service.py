"""Service for retrying buffered events"""
from datetime import datetime
from sqlalchemy import select
from app.core.database import db_manager
from app.core.queue import queue_manager
from app.core.logging import get_logger
from app.models.event_buffer import EventBuffer, EventBufferStatus

logger = get_logger(__name__)


class EventBufferService:
    """Manages retry of buffered events"""

    async def retry_buffered_events(self):
        """Retry events that failed to queue initially"""
        try:
            async with db_manager.AsyncSessionLocal() as db:
                # Get pending events with retry count less than 10
                stmt = (
                    select(EventBuffer)
                    .where(EventBuffer.status == EventBufferStatus.PENDING)
                    .where(EventBuffer.retry_count < 10)
                    .order_by(EventBuffer.created_at)
                    .limit(100)  # Process 100 at a time
                )

                result = await db.execute(stmt)
                buffered_events = result.scalars().all()

                if not buffered_events:
                    logger.debug("no_buffered_events_to_retry")
                    return {"processed": 0, "succeeded": 0, "failed": 0}

                processed = 0
                succeeded = 0
                failed = 0

                for event in buffered_events:
                    processed += 1

                    # Try to republish to queue
                    try:
                        event_data = event.event_data

                        # Determine routing key based on event type
                        if event.event_type in ["app_uninstalled", "tokens_revoked"]:
                            routing_key = f"slack.event.{event.event_type}"
                            message = {
                                "event_type": event.event_type,
                                "team_id": event.team_id,
                                "event_time": event_data.get("event_time")
                            }
                            if event.event_type == "tokens_revoked":
                                message["tokens"] = event_data.get("tokens", {})
                        else:
                            # Event callback
                            slack_event = event_data.get("event", {})
                            routing_key = f"slack.event.{event.event_type}"
                            message = {
                                "event": slack_event,
                                "team_id": event.team_id,
                                "event_id": event.event_id,
                                "event_time": event_data.get("event_time")
                            }

                        success = await queue_manager.publish(
                            queue=queue_manager.EVENTS_QUEUE,
                            message=message,
                            routing_key=routing_key
                        )

                        if success:
                            # Successfully queued - mark as queued
                            event.status = EventBufferStatus.QUEUED
                            event.processed_at = datetime.utcnow()
                            succeeded += 1
                            logger.info(
                                "buffered_event_requeued",
                                event_id=event.event_id,
                                event_type=event.event_type,
                                retry_count=event.retry_count
                            )
                        else:
                            # Queue publish failed - increment retry count
                            event.retry_count += 1
                            event.error_message = "Queue publish returned False"

                            if event.retry_count >= 10:
                                event.status = EventBufferStatus.FAILED
                                failed += 1
                                logger.error(
                                    "buffered_event_max_retries",
                                    event_id=event.event_id,
                                    event_type=event.event_type
                                )
                            else:
                                logger.warning(
                                    "buffered_event_retry_failed",
                                    event_id=event.event_id,
                                    retry_count=event.retry_count
                                )

                    except Exception as e:
                        # Exception during republish - increment retry count
                        event.retry_count += 1
                        event.error_message = str(e)

                        if event.retry_count >= 10:
                            event.status = EventBufferStatus.FAILED
                            failed += 1
                            logger.error(
                                "buffered_event_max_retries_exception",
                                event_id=event.event_id,
                                error=str(e)
                            )
                        else:
                            logger.warning(
                                "buffered_event_retry_exception",
                                event_id=event.event_id,
                                retry_count=event.retry_count,
                                error=str(e)
                            )

                # Commit all changes
                await db.commit()

                logger.info(
                    "buffered_events_retry_complete",
                    processed=processed,
                    succeeded=succeeded,
                    failed=failed
                )

                return {
                    "processed": processed,
                    "succeeded": succeeded,
                    "failed": failed
                }

        except Exception as e:
            logger.error("retry_buffered_events_error", error=str(e))
            return {"error": str(e)}

    async def cleanup_old_processed_events(self):
        """Clean up processed events older than 7 days"""
        try:
            from datetime import timedelta
            from app.models.processed_event import ProcessedEvent
            from sqlalchemy import delete

            cutoff_date = datetime.utcnow() - timedelta(days=7)

            async with db_manager.AsyncSessionLocal() as db:
                stmt = delete(ProcessedEvent).where(
                    ProcessedEvent.processed_at < cutoff_date
                )
                result = await db.execute(stmt)
                await db.commit()

                deleted_count = result.rowcount

                logger.info(
                    "old_processed_events_cleaned",
                    deleted_count=deleted_count,
                    cutoff_date=cutoff_date.isoformat()
                )

                return {"deleted": deleted_count}

        except Exception as e:
            logger.error("cleanup_old_processed_events_error", error=str(e))
            return {"error": str(e)}

    async def get_buffer_stats(self) -> dict:
        """Get statistics about buffered events"""
        try:
            async with db_manager.AsyncSessionLocal() as db:
                # Count by status
                pending_stmt = select(EventBuffer).where(EventBuffer.status == EventBufferStatus.PENDING)
                queued_stmt = select(EventBuffer).where(EventBuffer.status == EventBufferStatus.QUEUED)
                failed_stmt = select(EventBuffer).where(EventBuffer.status == EventBufferStatus.FAILED)

                pending_result = await db.execute(pending_stmt)
                queued_result = await db.execute(queued_stmt)
                failed_result = await db.execute(failed_stmt)

                pending_count = len(pending_result.scalars().all())
                queued_count = len(queued_result.scalars().all())
                failed_count = len(failed_result.scalars().all())

                return {
                    "pending": pending_count,
                    "queued": queued_count,
                    "failed": failed_count,
                    "total": pending_count + queued_count + failed_count
                }

        except Exception as e:
            logger.error("get_buffer_stats_error", error=str(e))
            return {"error": str(e)}


# Global instance
event_buffer_service = EventBufferService()
