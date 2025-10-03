"""Admin endpoints for backfill and sync operations"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.backfill_service import backfill_service
from app.services.sync_service import sync_service
from app.core.logging import get_logger

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
