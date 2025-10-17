"""Admin endpoints for backfill and sync operations"""
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from app.core.database import get_db
from app.services.backfill_service import backfill_service
from app.services.sync_service import sync_service
from app.services.event_buffer_service import event_buffer_service
from app.core.queue import queue_manager
from app.core.logging import get_logger
from app.models.event_buffer import EventBuffer, EventBufferStatus
from app.models.processed_event import ProcessedEvent

logger = get_logger(__name__)
router = APIRouter()


@router.post("/backfill/channel/{channel_id}")
async def backfill_channel(
    channel_id: str,
    background_tasks: BackgroundTasks,
    limit: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Trigger backfill for a specific channel"""

    async def run_backfill():
        async with db_manager.AsyncSessionLocal() as session:
            count = await backfill_service.backfill_channel(
                channel_id,
                session,
                limit_messages=limit
            )
            logger.info("channel_backfill_completed", channel_id=channel_id, count=count)

    from app.core.database import db_manager
    background_tasks.add_task(run_backfill)

    return {
        "status": "started",
        "channel_id": channel_id,
        "limit": limit
    }


@router.post("/backfill/all")
async def backfill_all_channels(
    background_tasks: BackgroundTasks,
    limit_per_channel: Optional[int] = 1000
) -> Dict[str, Any]:
    """Trigger backfill for all channels"""

    async def run_backfill():
        from app.workers.backfill_worker import backfill_worker
        await backfill_worker.run_full_backfill(limit_per_channel)

    background_tasks.add_task(run_backfill)

    return {
        "status": "started",
        "limit_per_channel": limit_per_channel
    }


@router.post("/backfill/resume")
async def resume_backfill(
    background_tasks: BackgroundTasks
) -> Dict[str, str]:
    """Resume interrupted backfills"""

    async def run_resume():
        from app.workers.backfill_worker import backfill_worker
        await backfill_worker.resume_backfill()

    background_tasks.add_task(run_resume)

    return {"status": "resuming"}


@router.post("/sync/channels")
async def sync_channels(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    """Sync all channels"""

    async def run_sync():
        async with db_manager.AsyncSessionLocal() as session:
            count = await sync_service.sync_all_channels(session)
            logger.info("channels_sync_completed", count=count)

    from app.core.database import db_manager
    background_tasks.add_task(run_sync)

    return {"status": "started"}


@router.post("/sync/channel/{channel_id}")
async def sync_channel(
    channel_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Sync specific channel"""
    channel = await sync_service.sync_channel(channel_id, db)

    if channel:
        return {
            "status": "synced",
            "channel_id": channel.channel_id,
            "channel_name": channel.channel_name
        }
    else:
        return {"status": "failed", "channel_id": channel_id}


@router.post("/sync/user/{user_id}")
async def sync_user(
    user_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Sync specific user"""
    user = await sync_service.sync_user(user_id, db)

    if user:
        return {
            "status": "synced",
            "user_id": user.user_id,
            "username": user.username
        }
    else:
        return {"status": "failed", "user_id": user_id}


# ==========================================
# EMERGENCY RECOVERY & MONITORING ENDPOINTS
# ==========================================

@router.get("/health")
async def system_health(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """
    Overall system health check
    Shows status of queues, event buffer, workers, and dependencies
    """
    try:
        # Get buffer stats
        buffer_stats = await event_buffer_service.get_buffer_stats()

        # Get processed events count (last 24h)
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(hours=24)
        processed_count_stmt = select(func.count(ProcessedEvent.event_id)).where(
            ProcessedEvent.processed_at >= cutoff
        )
        result = await db.execute(processed_count_stmt)
        processed_24h = result.scalar()

        # Check queue manager initialization
        queue_status = "healthy" if queue_manager._initialized else "not_initialized"

        return {
            "status": "healthy" if buffer_stats.get("failed", 0) == 0 else "degraded",
            "timestamp": datetime.utcnow().isoformat(),
            "event_buffer": {
                "pending": buffer_stats.get("pending", 0),
                "queued": buffer_stats.get("queued", 0),
                "failed": buffer_stats.get("failed", 0),
                "total": buffer_stats.get("total", 0),
                "status": "ok" if buffer_stats.get("failed", 0) == 0 else "⚠️ has_failures"
            },
            "events_processed_24h": processed_24h,
            "rabbitmq": {
                "status": queue_status,
                "initialized": queue_manager._initialized
            },
            "database": "healthy",
            "alerts": [
                "⚠️ Event buffer has failed events" if buffer_stats.get("failed", 0) > 0 else None,
                "⚠️ Too many pending events" if buffer_stats.get("pending", 0) > 100 else None
            ]
        }
    except Exception as e:
        logger.error("health_check_error", error=str(e))
        return {
            "status": "error",
            "error": str(e)
        }


@router.get("/buffer/stats")
async def get_buffer_stats() -> Dict[str, Any]:
    """Get event buffer statistics"""
    return await event_buffer_service.get_buffer_stats()


@router.get("/buffer/failed")
async def get_failed_buffered_events(
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get all failed buffered events that need manual review"""
    try:
        stmt = (
            select(EventBuffer)
            .where(EventBuffer.status == EventBufferStatus.FAILED)
            .order_by(EventBuffer.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        failed_events = result.scalars().all()

        return {
            "total": len(failed_events),
            "events": [
                {
                    "id": event.id,
                    "event_id": event.event_id,
                    "team_id": event.team_id,
                    "event_type": event.event_type,
                    "retry_count": event.retry_count,
                    "error_message": event.error_message,
                    "created_at": event.created_at.isoformat(),
                    "event_data": event.event_data
                }
                for event in failed_events
            ]
        }
    except Exception as e:
        logger.error("get_failed_buffered_events_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/buffer/retry/{buffer_id}")
async def retry_single_buffered_event(
    buffer_id: int,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Manually retry a single buffered event"""
    try:
        # Get the buffered event
        result = await db.execute(
            select(EventBuffer).where(EventBuffer.id == buffer_id)
        )
        event = result.scalar_one_or_none()

        if not event:
            raise HTTPException(status_code=404, detail=f"Buffered event {buffer_id} not found")

        # Try to republish
        event_data = event.event_data

        # Determine routing key
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
            event.status = EventBufferStatus.QUEUED
            event.processed_at = datetime.utcnow()
            await db.commit()

            logger.info("manual_buffer_retry_success", buffer_id=buffer_id)
            return {
                "status": "success",
                "buffer_id": buffer_id,
                "message": "Event successfully requeued"
            }
        else:
            logger.error("manual_buffer_retry_failed", buffer_id=buffer_id)
            return {
                "status": "failed",
                "buffer_id": buffer_id,
                "message": "Queue publish returned False"
            }

    except Exception as e:
        logger.error("retry_buffered_event_error", buffer_id=buffer_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/buffer/retry-failed")
async def retry_all_failed_buffered_events(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Retry all failed buffered events (use after fixing the root cause)"""
    try:
        # Reset all failed events to pending with retry_count = 0
        stmt = (
            select(EventBuffer)
            .where(EventBuffer.status == EventBufferStatus.FAILED)
        )
        result = await db.execute(stmt)
        failed_events = result.scalars().all()

        for event in failed_events:
            event.status = EventBufferStatus.PENDING
            event.retry_count = 0
            event.error_message = "Manually reset for retry"

        await db.commit()

        logger.info("manual_retry_all_failed", count=len(failed_events))

        return {
            "status": "success",
            "message": f"Reset {len(failed_events)} failed events to pending. They will be retried by the background job.",
            "count": len(failed_events)
        }

    except Exception as e:
        logger.error("retry_all_failed_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/buffer/pending")
async def get_pending_buffered_events(
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get all pending buffered events"""
    try:
        stmt = (
            select(EventBuffer)
            .where(EventBuffer.status == EventBufferStatus.PENDING)
            .order_by(EventBuffer.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        pending_events = result.scalars().all()

        return {
            "total": len(pending_events),
            "events": [
                {
                    "id": event.id,
                    "event_id": event.event_id,
                    "team_id": event.team_id,
                    "event_type": event.event_type,
                    "retry_count": event.retry_count,
                    "created_at": event.created_at.isoformat()
                }
                for event in pending_events
            ]
        }
    except Exception as e:
        logger.error("get_pending_buffered_events_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/buffer/cleanup")
async def cleanup_old_buffered_events(
    days: int = 7,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Clean up old queued/failed buffered events"""
    try:
        from datetime import datetime, timedelta
        from sqlalchemy import delete

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Delete old queued events (successfully processed)
        stmt = delete(EventBuffer).where(
            EventBuffer.status == EventBufferStatus.QUEUED,
            EventBuffer.processed_at < cutoff_date
        )
        result = await db.execute(stmt)
        await db.commit()

        deleted_count = result.rowcount

        logger.info("buffer_cleanup_completed", deleted=deleted_count, days=days)

        return {
            "status": "success",
            "deleted": deleted_count,
            "cutoff_days": days
        }

    except Exception as e:
        logger.error("buffer_cleanup_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/processed-events/stats")
async def get_processed_events_stats(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Get statistics about processed events (for deduplication tracking)"""
    try:
        from datetime import datetime, timedelta

        # Total processed events
        total_stmt = select(func.count(ProcessedEvent.event_id))
        total_result = await db.execute(total_stmt)
        total = total_result.scalar()

        # Last 24 hours
        cutoff_24h = datetime.utcnow() - timedelta(hours=24)
        last_24h_stmt = select(func.count(ProcessedEvent.event_id)).where(
            ProcessedEvent.processed_at >= cutoff_24h
        )
        result_24h = await db.execute(last_24h_stmt)
        last_24h = result_24h.scalar()

        # Last 7 days
        cutoff_7d = datetime.utcnow() - timedelta(days=7)
        last_7d_stmt = select(func.count(ProcessedEvent.event_id)).where(
            ProcessedEvent.processed_at >= cutoff_7d
        )
        result_7d = await db.execute(last_7d_stmt)
        last_7d = result_7d.scalar()

        return {
            "total_processed_events": total,
            "last_24_hours": last_24h,
            "last_7_days": last_7d,
            "cleanup_policy": "Events older than 7 days are automatically cleaned up"
        }

    except Exception as e:
        logger.error("processed_events_stats_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
