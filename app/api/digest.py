"""
Daily Digest API Endpoints

Provides endpoints for:
- Getting user digest preferences
- Updating digest settings
- Muting/unmuting digests
- Triggering immediate digest (admin)
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.user_preferences import UserPreferences
from app.services.digest_scheduler import digest_scheduler
from app.services.personalized_digest_service import PersonalizedDigestService

logger = get_logger(__name__)
router = APIRouter(prefix="/api/digest", tags=["digest"])


# Request/Response Models
class DigestPreferencesResponse(BaseModel):
    """User's digest preferences"""
    user_id: str
    team_id: str
    daily_digest_enabled: bool
    digest_frequency: str
    digest_time_hour: int
    include_hot_topics: bool
    include_trending_discussions: bool
    include_mentions: bool
    include_my_threads: bool
    include_expert_insights: bool
    include_unanswered_questions: bool
    only_my_channels: bool
    min_priority_score: float


class UpdateDigestPreferencesRequest(BaseModel):
    """Request to update digest preferences"""
    daily_digest_enabled: Optional[bool] = None
    digest_frequency: Optional[str] = Field(None, pattern="^(daily|weekly|never)$")
    digest_time_hour: Optional[int] = Field(None, ge=0, le=23)
    include_hot_topics: Optional[bool] = None
    include_trending_discussions: Optional[bool] = None
    include_mentions: Optional[bool] = None
    include_my_threads: Optional[bool] = None
    include_expert_insights: Optional[bool] = None
    include_unanswered_questions: Optional[bool] = None
    only_my_channels: Optional[bool] = None
    min_priority_score: Optional[float] = Field(None, ge=0.0, le=1.0)


class TriggerDigestRequest(BaseModel):
    """Request to trigger immediate digest"""
    team_id: str
    hours_back: int = Field(default=24, ge=1, le=168)  # 1 hour to 1 week


class DigestStatusResponse(BaseModel):
    """Scheduler status"""
    is_running: bool
    next_run_time: Optional[str]
    scheduled_jobs: int


@router.get("/preferences/{user_id}/{team_id}", response_model=DigestPreferencesResponse)
async def get_digest_preferences(
    user_id: str,
    team_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's digest preferences

    Returns default preferences if user hasn't set any yet.
    """
    try:
        # Get or create preferences
        result = await db.execute(
            select(UserPreferences).where(
                UserPreferences.user_id == user_id,
                UserPreferences.team_id == team_id
            )
        )
        prefs = result.scalar_one_or_none()

        if not prefs:
            # Return defaults
            prefs = UserPreferences(
                user_id=user_id,
                team_id=team_id
            )

        return DigestPreferencesResponse(
            user_id=prefs.user_id,
            team_id=prefs.team_id,
            daily_digest_enabled=prefs.daily_digest_enabled,
            digest_frequency=prefs.digest_frequency,
            digest_time_hour=prefs.digest_time_hour,
            include_hot_topics=prefs.include_hot_topics,
            include_trending_discussions=prefs.include_trending_discussions,
            include_mentions=prefs.include_mentions,
            include_my_threads=prefs.include_my_threads,
            include_expert_insights=prefs.include_expert_insights,
            include_unanswered_questions=prefs.include_unanswered_questions,
            only_my_channels=prefs.only_my_channels,
            min_priority_score=prefs.min_priority_score
        )

    except Exception as e:
        logger.error("get_digest_preferences_error", error=str(e), user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get preferences: {str(e)}"
        )


@router.put("/preferences/{user_id}/{team_id}")
async def update_digest_preferences(
    user_id: str,
    team_id: str,
    request: UpdateDigestPreferencesRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Update user's digest preferences

    Only updates fields that are provided in the request.
    """
    try:
        # Get or create preferences
        result = await db.execute(
            select(UserPreferences).where(
                UserPreferences.user_id == user_id,
                UserPreferences.team_id == team_id
            )
        )
        prefs = result.scalar_one_or_none()

        if not prefs:
            prefs = UserPreferences(
                user_id=user_id,
                team_id=team_id
            )
            db.add(prefs)

        # Update fields that are provided
        update_data = request.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(prefs, field, value)

        await db.commit()
        await db.refresh(prefs)

        logger.info(
            "digest_preferences_updated",
            user_id=user_id,
            team_id=team_id,
            updated_fields=list(update_data.keys())
        )

        return {
            "status": "success",
            "message": "Preferences updated",
            "updated_fields": list(update_data.keys())
        }

    except Exception as e:
        logger.error("update_digest_preferences_error", error=str(e), user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update preferences: {str(e)}"
        )


@router.post("/mute/{user_id}/{team_id}")
async def mute_digest(
    user_id: str,
    team_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Mute daily digest for a user

    Quick action to disable digests without changing other preferences.
    """
    try:
        result = await db.execute(
            select(UserPreferences).where(
                UserPreferences.user_id == user_id,
                UserPreferences.team_id == team_id
            )
        )
        prefs = result.scalar_one_or_none()

        if not prefs:
            prefs = UserPreferences(
                user_id=user_id,
                team_id=team_id
            )
            db.add(prefs)

        prefs.daily_digest_enabled = False

        await db.commit()

        logger.info("digest_muted", user_id=user_id, team_id=team_id)

        return {
            "status": "success",
            "message": "Daily digest muted"
        }

    except Exception as e:
        logger.error("mute_digest_error", error=str(e), user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to mute digest: {str(e)}"
        )


@router.post("/unmute/{user_id}/{team_id}")
async def unmute_digest(
    user_id: str,
    team_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Unmute daily digest for a user

    Re-enables digests with existing preferences.
    """
    try:
        result = await db.execute(
            select(UserPreferences).where(
                UserPreferences.user_id == user_id,
                UserPreferences.team_id == team_id
            )
        )
        prefs = result.scalar_one_or_none()

        if not prefs:
            prefs = UserPreferences(
                user_id=user_id,
                team_id=team_id
            )
            db.add(prefs)

        prefs.daily_digest_enabled = True

        await db.commit()

        logger.info("digest_unmuted", user_id=user_id, team_id=team_id)

        return {
            "status": "success",
            "message": "Daily digest unmuted"
        }

    except Exception as e:
        logger.error("unmute_digest_error", error=str(e), user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to unmute digest: {str(e)}"
        )


@router.post("/trigger")
async def trigger_immediate_digest(
    request: TriggerDigestRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Trigger immediate digest send for a workspace

    Admin/testing endpoint to manually send digests without waiting for schedule.
    """
    try:
        sent_count = await digest_scheduler.trigger_immediate(
            team_id=request.team_id,
            hours_back=request.hours_back
        )

        logger.info(
            "immediate_digest_sent",
            team_id=request.team_id,
            sent_count=sent_count
        )

        return {
            "status": "success",
            "message": f"Sent {sent_count} digests",
            "sent_count": sent_count
        }

    except Exception as e:
        logger.error("trigger_digest_error", error=str(e), team_id=request.team_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger digest: {str(e)}"
        )


@router.get("/status", response_model=DigestStatusResponse)
async def get_scheduler_status():
    """
    Get digest scheduler status

    Shows whether scheduler is running and next run time.
    """
    try:
        status_info = digest_scheduler.get_status()

        return DigestStatusResponse(
            is_running=status_info['is_running'],
            next_run_time=status_info['next_run_time'],
            scheduled_jobs=status_info['scheduled_jobs']
        )

    except Exception as e:
        logger.error("get_scheduler_status_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get status: {str(e)}"
        )


@router.post("/preview/{user_id}/{team_id}")
async def preview_digest(
    user_id: str,
    team_id: str,
    hours_back: int = 24,
    db: AsyncSession = Depends(get_db)
):
    """
    Preview digest content without sending

    Useful for testing what a user would receive.
    """
    try:
        digest_service = PersonalizedDigestService(db)

        digest = await digest_service.generate_personalized_digest(
            user_id=user_id,
            team_id=team_id,
            hours_back=hours_back
        )

        if not digest:
            return {
                "status": "no_content",
                "message": "Digest is muted or no content available"
            }

        return {
            "status": "success",
            "digest": digest
        }

    except Exception as e:
        logger.error("preview_digest_error", error=str(e), user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to preview digest: {str(e)}"
        )
