"""Background worker for scheduled notifications"""
from datetime import datetime, timedelta
from typing import List
from sqlalchemy import select, and_
from app.models.workspace import Workspace
from app.core.database import db_manager
from app.core.logging import get_logger
from app.services.notification_service import notification_service

logger = get_logger(__name__)


class NotificationWorker:
    """Handles scheduled notification jobs"""

    async def check_trial_expirations(self) -> None:
        """Check for trials expiring soon and send warnings"""

        try:
            logger.info("trial_expiration_check_started")

            async for db in db_manager.get_session():
                now = datetime.utcnow()

                # Check for trials expiring in 7, 3, and 1 days
                warning_windows = [
                    (7, "7_day_warning"),
                    (3, "3_day_warning"),
                    (1, "1_day_warning")
                ]

                total_warnings_sent = 0

                for days, warning_type in warning_windows:
                    target_date = now + timedelta(days=days)
                    start_of_day = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                    end_of_day = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)

                    # Find workspaces with trials expiring on target date
                    stmt = select(Workspace).where(
                        and_(
                            Workspace.subscription_status == 'trial',
                            Workspace.trial_ends_at >= start_of_day,
                            Workspace.trial_ends_at <= end_of_day,
                            Workspace.is_active == 1
                        )
                    )

                    result = await db.execute(stmt)
                    workspaces = result.scalars().all()

                    logger.info(
                        "trial_warnings_found",
                        warning_type=warning_type,
                        count=len(workspaces)
                    )

                    # Send warnings
                    for workspace in workspaces:
                        try:
                            await notification_service.send_trial_expiration_warning(
                                workspace=workspace,
                                days_remaining=days,
                                db=db
                            )
                            total_warnings_sent += 1

                        except Exception as e:
                            logger.error(
                                "trial_warning_send_failed",
                                error=str(e),
                                team_id=workspace.team_id,
                                warning_type=warning_type
                            )

                logger.info(
                    "trial_expiration_check_completed",
                    warnings_sent=total_warnings_sent
                )

        except Exception as e:
            logger.error(
                "trial_expiration_check_error",
                error=str(e)
            )
            raise

    async def check_expired_trials(self) -> None:
        """Check for expired trials and send final notifications"""

        try:
            logger.info("expired_trial_check_started")

            async for db in db_manager.get_session():
                now = datetime.utcnow()

                # Find trials that expired in the last 24 hours (not already processed)
                yesterday = now - timedelta(days=1)

                stmt = select(Workspace).where(
                    and_(
                        Workspace.subscription_status == 'trial',
                        Workspace.trial_ends_at < now,
                        Workspace.trial_ends_at >= yesterday,
                        Workspace.is_active == 1
                    )
                )

                result = await db.execute(stmt)
                expired_workspaces = result.scalars().all()

                logger.info(
                    "expired_trials_found",
                    count=len(expired_workspaces)
                )

                notifications_sent = 0

                for workspace in expired_workspaces:
                    try:
                        # Send expiration notification
                        await notification_service.send_trial_expired_notification(
                            workspace=workspace,
                            db=db
                        )

                        # Update subscription status to expired
                        workspace.subscription_status = "expired"
                        await db.commit()

                        notifications_sent += 1

                        logger.info(
                            "trial_expired_processed",
                            team_id=workspace.team_id,
                            team_name=workspace.team_name
                        )

                    except Exception as e:
                        await db.rollback()
                        logger.error(
                            "expired_trial_notification_failed",
                            error=str(e),
                            team_id=workspace.team_id
                        )

                logger.info(
                    "expired_trial_check_completed",
                    notifications_sent=notifications_sent
                )

        except Exception as e:
            logger.error(
                "expired_trial_check_error",
                error=str(e)
            )
            raise

    async def reset_monthly_query_limits(self) -> None:
        """Reset monthly query limits for all active subscriptions"""

        try:
            logger.info("query_limit_reset_started")

            async for db in db_manager.get_session():
                now = datetime.utcnow()

                # Find workspaces where billing period has rolled over
                stmt = select(Workspace).where(
                    and_(
                        Workspace.subscription_status == 'active',
                        Workspace.current_period_end < now,
                        Workspace.is_active == 1
                    )
                )

                result = await db.execute(stmt)
                workspaces = result.scalars().all()

                reset_count = 0

                for workspace in workspaces:
                    try:
                        # Reset query counter
                        workspace.reset_monthly_queries()
                        await db.commit()

                        reset_count += 1

                        logger.info(
                            "query_limit_reset",
                            team_id=workspace.team_id,
                            previous_usage=workspace.queries_used_this_month
                        )

                    except Exception as e:
                        await db.rollback()
                        logger.error(
                            "query_limit_reset_failed",
                            error=str(e),
                            team_id=workspace.team_id
                        )

                logger.info(
                    "query_limit_reset_completed",
                    workspaces_reset=reset_count
                )

        except Exception as e:
            logger.error(
                "query_limit_reset_error",
                error=str(e)
            )
            raise

    async def sync_workspace_user_counts(self) -> None:
        """Sync user counts from Slack and detect tier changes"""

        try:
            logger.info("user_count_sync_started")

            async for db in db_manager.get_session():
                # Get all active workspaces
                stmt = select(Workspace).where(Workspace.is_active == 1)
                result = await db.execute(stmt)
                workspaces = result.scalars().all()

                synced_count = 0
                tier_changes = 0

                for workspace in workspaces:
                    # Store team_id early to avoid expired attribute access
                    team_id = workspace.team_id
                    bot_token = workspace.bot_access_token

                    try:
                        # Fetch current user count from Slack
                        import httpx
                        async with httpx.AsyncClient() as client:
                            response = await client.post(
                                "https://slack.com/api/users.list",
                                headers={"Authorization": f"Bearer {bot_token}"}
                            )

                            # Check HTTP status first
                            if response.status_code != 200:
                                logger.error(
                                    "slack_api_http_error",
                                    team_id=team_id,
                                    status_code=response.status_code,
                                    response_text=response.text[:200]
                                )
                                continue

                            data = response.json()

                            if data.get("ok"):
                                # Count active, non-bot users
                                active_users = [
                                    u for u in data.get("members", [])
                                    if not u.get("is_bot") and not u.get("deleted")
                                ]
                                new_user_count = len(active_users)

                                old_user_count = workspace.user_count
                                old_tier = workspace.subscription_tier

                                # Update user count
                                workspace.user_count = new_user_count
                                workspace.active_user_count = new_user_count
                                workspace.last_user_count_sync = datetime.utcnow()

                                # Check if tier should change
                                from app.services.oauth_service import oauth_service
                                recommended_tier = oauth_service.determine_pricing_tier(new_user_count)

                                # Only auto-upgrade if user count increased significantly
                                tier_order = {"starter": 1, "growth": 2, "business": 3, "enterprise": 4}
                                if tier_order.get(recommended_tier, 0) > tier_order.get(old_tier, 0):
                                    logger.info(
                                        "tier_upgrade_recommended",
                                        team_id=workspace.team_id,
                                        old_tier=old_tier,
                                        recommended_tier=recommended_tier,
                                        old_user_count=old_user_count,
                                        new_user_count=new_user_count
                                    )
                                    # TODO: Send notification to admin about recommended tier upgrade
                                    tier_changes += 1

                                await db.commit()
                                synced_count += 1

                            else:
                                logger.error(
                                    "slack_user_list_failed",
                                    team_id=team_id,
                                    error=data.get("error")
                                )

                    except Exception as e:
                        await db.rollback()
                        logger.error(
                            "user_count_sync_failed",
                            error=str(e),
                            team_id=team_id
                        )

                logger.info(
                    "user_count_sync_completed",
                    workspaces_synced=synced_count,
                    tier_changes_detected=tier_changes
                )

        except Exception as e:
            logger.error(
                "user_count_sync_error",
                error=str(e)
            )
            raise


# Global notification worker instance
notification_worker = NotificationWorker()
