"""Admin dashboard API endpoints for workspace management"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from app.core.database import get_db
from app.models.workspace import Workspace, InstallationLog
from app.models.channel import Channel
from app.models.message import Message
from app.models.user import User
from app.services.workspace_service import workspace_service
from app.core.auth import get_current_admin_user, get_current_user
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class WorkspaceSettingsUpdate(BaseModel):
    """Update workspace settings"""
    index_private_channels: Optional[bool] = None
    index_archived_channels: Optional[bool] = None
    excluded_channel_ids: Optional[List[str]] = None
    slack_dm_notifications: Optional[bool] = None
    email_notifications: Optional[bool] = None
    weekly_digest: Optional[bool] = None


class ChannelIndexingUpdate(BaseModel):
    """Update channel indexing settings"""
    channel_id: str
    indexing_enabled: bool


@router.get("/admin/dashboard/overview/{team_id}")
async def get_dashboard_overview(
    team_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Get comprehensive dashboard overview for workspace (admin only)"""

    # Verify user has access to this workspace
    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    try:
        # Get workspace
        workspace = await workspace_service.get_workspace_by_team_id(team_id, db)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Get query statistics
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)
        month_start = today_start - timedelta(days=30)

        # Count indexed channels and messages
        channel_stmt = select(func.count(Channel.id)).where(
            and_(
                Channel.team_id == team_id,
                Channel.is_indexed == 1
            )
        )
        channel_result = await db.execute(channel_stmt)
        indexed_channels_count = channel_result.scalar()

        message_stmt = select(func.count(Message.id)).where(Message.team_id == team_id)
        message_result = await db.execute(message_stmt)
        total_messages_count = message_result.scalar()

        # Calculate trial/subscription info
        trial_days_remaining = None
        subscription_ends_at = None
        if workspace.subscription_status == "trial" and workspace.trial_ends_at:
            days_left = (workspace.trial_ends_at - now).days
            trial_days_remaining = max(0, days_left)
        elif workspace.subscription_status == "active" and workspace.current_period_end:
            subscription_ends_at = workspace.current_period_end.isoformat()

        # Query usage stats
        queries_remaining = workspace.monthly_query_limit - workspace.queries_used_this_month
        usage_percentage = (workspace.queries_used_this_month / workspace.monthly_query_limit * 100) if workspace.monthly_query_limit > 0 else 0

        return {
            "workspace": {
                "team_id": workspace.team_id,
                "team_name": workspace.team_name,
                "team_domain": workspace.team_domain,
                "installed_at": workspace.installed_at.isoformat(),
                "is_active": bool(workspace.is_active),
                "user_count": workspace.user_count,
                "active_user_count": workspace.active_user_count
            },
            "subscription": {
                "status": workspace.subscription_status,
                "tier": workspace.subscription_tier,
                "is_active": workspace.is_subscription_active,
                "trial_days_remaining": trial_days_remaining,
                "subscription_ends_at": subscription_ends_at,
                "monthly_query_limit": workspace.monthly_query_limit,
                "queries_used_this_month": workspace.queries_used_this_month,
                "queries_remaining": queries_remaining,
                "usage_percentage": round(usage_percentage, 2),
                "billing_status": "good" if workspace.subscription_status in ["trial", "active"] else "attention_needed"
            },
            "indexing": {
                "status": workspace.indexing_status,
                "channels_indexed": indexed_channels_count,
                "messages_indexed": total_messages_count,
                "last_indexed": workspace.indexing_completed_at.isoformat() if workspace.indexing_completed_at else None
            },
            "activity": {
                "last_activity": workspace.last_activity_at.isoformat() if workspace.last_activity_at else None,
                "total_queries_all_time": workspace.total_queries_all_time,
                "queries_this_month": workspace.queries_used_this_month
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("dashboard_overview_error", error=str(e), team_id=team_id)
        raise HTTPException(status_code=500, detail="Failed to load dashboard")


@router.get("/admin/dashboard/analytics/{team_id}")
async def get_workspace_analytics(
    team_id: str,
    current_user: User = Depends(get_current_admin_user),
    days: int = Query(default=30, ge=1, le=90),
    db: AsyncSession = Depends(get_db)
):
    """Get detailed analytics for workspace (admin only)"""

    # Verify access
    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    try:
        workspace = await workspace_service.get_workspace_by_team_id(team_id, db)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Query usage trends (simulated - in production, track in separate table)
        # For now, return summary data

        # Get top channels by message count
        top_channels_stmt = (
            select(Channel.channel_name, func.count(Message.id).label('message_count'))
            .join(Message, Message.channel_id == Channel.channel_id)
            .where(Channel.team_id == team_id)
            .group_by(Channel.channel_name)
            .order_by(desc('message_count'))
            .limit(10)
        )
        top_channels_result = await db.execute(top_channels_stmt)
        top_channels = [
            {"channel": row[0], "messages": row[1]}
            for row in top_channels_result.fetchall()
        ]

        # Installation events timeline
        logs_stmt = (
            select(InstallationLog)
            .where(InstallationLog.team_id == team_id)
            .order_by(desc(InstallationLog.created_at))
            .limit(20)
        )
        logs_result = await db.execute(logs_stmt)
        logs = logs_result.scalars().all()

        event_timeline = [
            {
                "event_type": log.event_type,
                "timestamp": log.created_at.isoformat(),
                "data": log.event_data
            }
            for log in logs
        ]

        return {
            "period_days": days,
            "subscription": {
                "tier": workspace.subscription_tier,
                "query_limit": workspace.monthly_query_limit,
                "queries_used": workspace.queries_used_this_month,
                "usage_percentage": workspace.query_limit_percentage
            },
            "top_channels": top_channels,
            "event_timeline": event_timeline,
            "metrics": {
                "total_channels": workspace.total_channels_indexed,
                "total_messages": workspace.total_messages_indexed,
                "total_queries": workspace.total_queries_all_time,
                "avg_queries_per_day": round(workspace.queries_used_this_month / 30, 2) if workspace.queries_used_this_month > 0 else 0
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("analytics_error", error=str(e), team_id=team_id)
        raise HTTPException(status_code=500, detail="Failed to load analytics")


@router.get("/admin/dashboard/channels/{team_id}")
async def get_workspace_channels(
    team_id: str,
    current_user: User = Depends(get_current_admin_user),
    include_archived: bool = Query(default=False),
    db: AsyncSession = Depends(get_db)
):
    """Get all channels for workspace with indexing status (admin only)"""

    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    try:
        workspace = await workspace_service.get_workspace_by_team_id(team_id, db)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Build query
        stmt = select(Channel).where(Channel.team_id == team_id)

        if not include_archived:
            stmt = stmt.where(Channel.is_archived == 0)

        stmt = stmt.order_by(Channel.channel_name)

        result = await db.execute(stmt)
        channels = result.scalars().all()

        # Count messages per channel
        channel_data = []
        for channel in channels:
            msg_stmt = select(func.count(Message.id)).where(Message.channel_id == channel.channel_id)
            msg_result = await db.execute(msg_stmt)
            message_count = msg_result.scalar()

            channel_data.append({
                "channel_id": channel.channel_id,
                "channel_name": channel.channel_name,
                "is_private": bool(channel.is_private),
                "is_archived": bool(channel.is_archived),
                "member_count": channel.member_count,
                "is_indexed": bool(channel.is_indexed),
                "indexing_enabled": bool(channel.indexing_enabled),
                "indexing_status": channel.indexing_status,
                "message_count": message_count,
                "last_indexed": channel.last_message_indexed_ts,
                "created_at": channel.created_at.isoformat() if channel.created_at else None
            })

        return {
            "team_id": team_id,
            "total_channels": len(channel_data),
            "channels": channel_data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("channels_list_error", error=str(e), team_id=team_id)
        raise HTTPException(status_code=500, detail="Failed to load channels")


@router.put("/admin/dashboard/channels/{team_id}/indexing")
async def update_channel_indexing(
    team_id: str,
    update: ChannelIndexingUpdate,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Enable or disable indexing for a specific channel (admin only)"""

    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    try:
        workspace = await workspace_service.get_workspace_by_team_id(team_id, db)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Get channel
        stmt = select(Channel).where(
            and_(
                Channel.team_id == team_id,
                Channel.channel_id == update.channel_id
            )
        )
        result = await db.execute(stmt)
        channel = result.scalar_one_or_none()

        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")

        # Update indexing setting
        channel.indexing_enabled = 1 if update.indexing_enabled else 0
        channel.updated_at = datetime.utcnow()

        await db.commit()

        logger.info(
            "channel_indexing_updated",
            team_id=team_id,
            channel_id=update.channel_id,
            enabled=update.indexing_enabled
        )

        return {
            "success": True,
            "channel_id": update.channel_id,
            "channel_name": channel.channel_name,
            "indexing_enabled": update.indexing_enabled
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("channel_indexing_update_error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update channel indexing")


@router.get("/admin/dashboard/settings/{team_id}")
async def get_workspace_settings(
    team_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Get workspace settings (admin only)"""

    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    try:
        workspace = await workspace_service.get_workspace_by_team_id(team_id, db)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        settings = workspace.settings or {}

        return {
            "team_id": team_id,
            "team_name": workspace.team_name,
            "privacy": settings.get("privacy", {
                "index_private_channels": True,
                "index_archived_channels": False,
                "excluded_channel_ids": []
            }),
            "notifications": settings.get("notifications", {
                "slack_dm": True,
                "email": True,
                "weekly_digest": True
            }),
            "features": settings.get("features", {
                "ai_answers": True,
                "code_search": True,
                "analytics": True
            })
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("settings_get_error", error=str(e), team_id=team_id)
        raise HTTPException(status_code=500, detail="Failed to load settings")


@router.put("/admin/dashboard/settings/{team_id}")
async def update_workspace_settings(
    team_id: str,
    update: WorkspaceSettingsUpdate,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Update workspace settings (admin only)"""

    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    try:
        workspace = await workspace_service.get_workspace_by_team_id(team_id, db)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Get current settings
        settings = workspace.settings or {}

        # Update privacy settings
        privacy = settings.get("privacy", {})
        if update.index_private_channels is not None:
            privacy["index_private_channels"] = update.index_private_channels
        if update.index_archived_channels is not None:
            privacy["index_archived_channels"] = update.index_archived_channels
        if update.excluded_channel_ids is not None:
            privacy["excluded_channel_ids"] = update.excluded_channel_ids

        # Update notification settings
        notifications = settings.get("notifications", {})
        if update.slack_dm_notifications is not None:
            notifications["slack_dm"] = update.slack_dm_notifications
        if update.email_notifications is not None:
            notifications["email"] = update.email_notifications
        if update.weekly_digest is not None:
            notifications["weekly_digest"] = update.weekly_digest

        # Save updated settings
        settings["privacy"] = privacy
        settings["notifications"] = notifications
        workspace.settings = settings
        workspace.updated_at = datetime.utcnow()

        await db.commit()

        logger.info("workspace_settings_updated", team_id=team_id)

        return {
            "success": True,
            "settings": {
                "privacy": privacy,
                "notifications": notifications
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("settings_update_error", error=str(e), team_id=team_id)
        raise HTTPException(status_code=500, detail="Failed to update settings")


@router.get("/admin/dashboard/usage/{team_id}")
async def get_usage_details(
    team_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Get detailed usage metrics (admin only)"""

    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    try:
        workspace = await workspace_service.get_workspace_by_team_id(team_id, db)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Calculate usage metrics
        usage_percentage = workspace.query_limit_percentage
        queries_remaining = workspace.monthly_query_limit - workspace.queries_used_this_month

        # Determine usage status
        if usage_percentage >= 100:
            status = "limit_reached"
            status_color = "red"
        elif usage_percentage >= 90:
            status = "critical"
            status_color = "orange"
        elif usage_percentage >= 80:
            status = "warning"
            status_color = "yellow"
        else:
            status = "healthy"
            status_color = "green"

        # Calculate projected usage
        if workspace.current_period_start:
            days_into_period = (datetime.utcnow() - workspace.current_period_start).days
            if days_into_period > 0:
                avg_per_day = workspace.queries_used_this_month / days_into_period
                days_in_period = 30  # Approximate
                projected_usage = avg_per_day * days_in_period
            else:
                projected_usage = 0
        else:
            projected_usage = 0

        return {
            "current_period": {
                "start": workspace.current_period_start.isoformat() if workspace.current_period_start else None,
                "end": workspace.current_period_end.isoformat() if workspace.current_period_end else None
            },
            "usage": {
                "queries_used": workspace.queries_used_this_month,
                "queries_limit": workspace.monthly_query_limit,
                "queries_remaining": queries_remaining,
                "usage_percentage": round(usage_percentage, 2),
                "projected_monthly_usage": round(projected_usage, 0),
                "status": status,
                "status_color": status_color
            },
            "all_time": {
                "total_queries": workspace.total_queries_all_time,
                "queries_reset_at": workspace.queries_reset_at.isoformat() if workspace.queries_reset_at else None
            },
            "tier_info": {
                "current_tier": workspace.subscription_tier,
                "monthly_limit": workspace.monthly_query_limit,
                "upgrade_recommendation": usage_percentage >= 80
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("usage_details_error", error=str(e), team_id=team_id)
        raise HTTPException(status_code=500, detail="Failed to load usage details")


@router.get("/admin/dashboard/billing/{team_id}")
async def get_billing_info(
    team_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Get billing and subscription information (admin only)"""

    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    try:
        workspace = await workspace_service.get_workspace_by_team_id(team_id, db)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Trial info
        trial_info = None
        if workspace.subscription_status == "trial":
            days_remaining = workspace.trial_days_remaining if workspace.trial_ends_at else 0
            trial_info = {
                "started_at": workspace.trial_started_at.isoformat() if workspace.trial_started_at else None,
                "ends_at": workspace.trial_ends_at.isoformat() if workspace.trial_ends_at else None,
                "days_remaining": days_remaining,
                "is_active": days_remaining > 0
            }

        # Subscription info
        subscription_info = None
        if workspace.stripe_subscription_id:
            subscription_info = {
                "subscription_id": workspace.stripe_subscription_id,
                "customer_id": workspace.stripe_customer_id,
                "status": workspace.subscription_status,
                "tier": workspace.subscription_tier,
                "current_period_start": workspace.current_period_start.isoformat() if workspace.current_period_start else None,
                "current_period_end": workspace.current_period_end.isoformat() if workspace.current_period_end else None
            }

        # Payment info
        payment_info = None
        if workspace.last_payment_date:
            payment_info = {
                "last_payment_date": workspace.last_payment_date.isoformat(),
                "last_payment_amount": workspace.last_payment_amount
            }

        return {
            "team_id": team_id,
            "subscription_status": workspace.subscription_status,
            "subscription_tier": workspace.subscription_tier,
            "trial": trial_info,
            "subscription": subscription_info,
            "payment": payment_info,
            "has_active_subscription": workspace.is_subscription_active
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("billing_info_error", error=str(e), team_id=team_id)
        raise HTTPException(status_code=500, detail="Failed to load billing info")
