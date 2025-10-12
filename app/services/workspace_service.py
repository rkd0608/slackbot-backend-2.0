"""Workspace management service"""
from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.workspace import Workspace
from app.core.logging import get_logger
from app.core.cache import cache_manager

logger = get_logger(__name__)


class WorkspaceService:
    """Service for workspace operations and validation"""

    async def get_workspace_by_team_id(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Optional[Workspace]:
        """Get workspace by team_id"""

        # Try cache first
        cache_key = f"workspace:{team_id}"
        cached = await cache_manager.get(cache_key)

        if cached:
            # Return from cache, but still need to query for SQLAlchemy object
            pass

        stmt = select(Workspace).where(Workspace.team_id == team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if workspace:
            # Cache for 5 minutes
            await cache_manager.set(
                cache_key,
                {
                    "team_id": workspace.team_id,
                    "team_name": workspace.team_name,
                    "subscription_status": workspace.subscription_status,
                    "is_active": workspace.is_active
                },
                ttl=300
            )

        return workspace

    async def validate_workspace_access(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Validate workspace has active subscription and can process queries"""

        workspace = await self.get_workspace_by_team_id(team_id, db)

        if not workspace:
            return {
                "valid": False,
                "error": "workspace_not_found",
                "message": "This workspace is not installed. Please install the app first."
            }

        if not workspace.is_active:
            return {
                "valid": False,
                "error": "workspace_inactive",
                "message": "This workspace has been deactivated. Please contact support."
            }

        if not workspace.is_subscription_active:
            if workspace.subscription_status == "trial" and workspace.trial_ends_at:
                if workspace.trial_ends_at < datetime.utcnow():
                    return {
                        "valid": False,
                        "error": "trial_expired",
                        "message": "Your trial has expired. Please upgrade to continue using the app."
                    }

            elif workspace.subscription_status in ["cancelled", "expired"]:
                return {
                    "valid": False,
                    "error": "subscription_inactive",
                    "message": "Your subscription is inactive. Please renew to continue."
                }

            elif workspace.subscription_status == "suspended":
                return {
                    "valid": False,
                    "error": "subscription_suspended",
                    "message": "Your subscription is suspended due to payment issues. Please update payment."
                }

        # Check query limits
        if not workspace.has_query_limit_available:
            return {
                "valid": False,
                "error": "query_limit_reached",
                "message": f"You've reached your monthly limit of {workspace.monthly_query_limit} queries. Upgrade for more!",
                "usage": {
                    "used": workspace.queries_used_this_month,
                    "limit": workspace.monthly_query_limit,
                    "percentage": workspace.query_limit_percentage
                }
            }

        return {
            "valid": True,
            "workspace": workspace,
            "usage": {
                "used": workspace.queries_used_this_month,
                "limit": workspace.monthly_query_limit,
                "percentage": workspace.query_limit_percentage
            }
        }

    async def increment_query_usage(
        self,
        team_id: str,
        db: AsyncSession
    ) -> None:
        """Increment query usage counter for workspace"""

        workspace = await self.get_workspace_by_team_id(team_id, db)

        if workspace:
            # Store previous percentage for threshold detection
            previous_percentage = workspace.query_limit_percentage

            workspace.increment_query_count()
            await db.commit()

            logger.info(
                "query_usage_incremented",
                team_id=team_id,
                queries_used=workspace.queries_used_this_month,
                limit=workspace.monthly_query_limit
            )

            # Check if warning thresholds crossed (80%, 90%, 100%)
            current_percentage = workspace.query_limit_percentage

            # Send notification if crossing threshold
            from app.services.notification_service import notification_service
            try:
                if current_percentage >= 100 and previous_percentage < 100:
                    # Hit 100% limit
                    await notification_service.send_query_limit_warning(
                        workspace=workspace,
                        percentage=current_percentage,
                        db=db
                    )
                elif current_percentage >= 90 and previous_percentage < 90:
                    # Hit 90% warning
                    await notification_service.send_query_limit_warning(
                        workspace=workspace,
                        percentage=current_percentage,
                        db=db
                    )
                elif current_percentage >= 80 and previous_percentage < 80:
                    # Hit 80% warning
                    await notification_service.send_query_limit_warning(
                        workspace=workspace,
                        percentage=current_percentage,
                        db=db
                    )
            except Exception as notify_error:
                logger.error("query_limit_notification_failed", error=str(notify_error))

            # Invalidate cache
            await cache_manager.delete(f"workspace:{team_id}")

    async def get_workspace_token(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Optional[str]:
        """Get bot access token for workspace"""

        workspace = await self.get_workspace_by_team_id(team_id, db)
        return workspace.bot_access_token if workspace else None

    async def update_workspace_activity(
        self,
        team_id: str,
        db: AsyncSession
    ) -> None:
        """Update last activity timestamp"""

        workspace = await self.get_workspace_by_team_id(team_id, db)

        if workspace:
            workspace.last_activity_at = datetime.utcnow()
            await db.commit()

    async def get_workspace_settings(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Get workspace settings"""

        workspace = await self.get_workspace_by_team_id(team_id, db)

        if not workspace:
            return {}

        return workspace.settings or {
            "privacy": {
                "index_private_channels": True,
                "index_archived_channels": False,
                "excluded_channel_ids": []
            },
            "notifications": {
                "slack_dm": True,
                "email": True,
                "weekly_digest": True
            },
            "features": {
                "ai_answers": True,
                "code_search": True,
                "analytics": True
            }
        }

    async def should_index_channel(
        self,
        team_id: str,
        channel_id: str,
        is_private: bool,
        is_archived: bool,
        db: AsyncSession
    ) -> bool:
        """Check if channel should be indexed based on workspace settings"""

        settings = await self.get_workspace_settings(team_id, db)
        privacy = settings.get("privacy", {})

        # Check exclusion list
        if channel_id in privacy.get("excluded_channel_ids", []):
            return False

        # Check private channel setting
        if is_private and not privacy.get("index_private_channels", True):
            return False

        # Check archived channel setting
        if is_archived and not privacy.get("index_archived_channels", False):
            return False

        return True


# Global workspace service instance
workspace_service = WorkspaceService()
