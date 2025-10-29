"""Handles workspace installation/uninstallation events"""
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.workspace import Workspace, InstallationLog
from app.core.logging import get_logger
from app.core.queue import queue_manager

logger = get_logger(__name__)


class InstallationHandler:
    """Handles workspace lifecycle events"""

    async def handle_app_uninstalled(
        self,
        team_id: str,
        db: AsyncSession
    ) -> None:
        """Handle app uninstallation event"""

        try:
            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                logger.warning("workspace_not_found_for_uninstall", team_id=team_id)
                return

            # Deactivate workspace (soft delete)
            workspace.is_active = 0
            workspace.deactivated_at = datetime.utcnow()
            workspace.updated_at = datetime.utcnow()

            # Log uninstallation
            log = InstallationLog(
                team_id=team_id,
                event_type="uninstalled",
                event_data={
                    "team_name": workspace.team_name,
                    "subscription_status": workspace.subscription_status,
                    "deactivated_at": workspace.deactivated_at.isoformat()
                },
                created_at=datetime.utcnow()
            )
            db.add(log)

            await db.commit()

            logger.info(
                "workspace_uninstalled",
                team_id=team_id,
                team_name=workspace.team_name
            )

            # Queue data retention job (delete after 7 days)
            await queue_manager.publish(
                queue=queue_manager.PROCESSING_QUEUE,
                message={
                    "task": "schedule_workspace_deletion",
                    "team_id": team_id,
                    "deletion_date": (datetime.utcnow() + timedelta(days=7)).isoformat()
                },
                routing_key="workspace.deletion.schedule"
            )

        except Exception as e:
            logger.error(
                "handle_uninstall_error",
                error=str(e),
                team_id=team_id
            )
            raise

    async def handle_tokens_revoked(
        self,
        team_id: str,
        tokens: dict,
        db: AsyncSession
    ) -> None:
        """Handle token revocation event"""

        try:
            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                logger.warning("workspace_not_found_for_token_revoke", team_id=team_id)
                return

            # Log token revocation
            log = InstallationLog(
                team_id=team_id,
                event_type="tokens_revoked",
                event_data={
                    "tokens": tokens,
                    "revoked_at": datetime.utcnow().isoformat()
                },
                created_at=datetime.utcnow()
            )
            db.add(log)

            # Deactivate workspace until reinstalled
            workspace.is_active = 0
            workspace.updated_at = datetime.utcnow()

            await db.commit()

            logger.info(
                "tokens_revoked",
                team_id=team_id,
                team_name=workspace.team_name
            )

        except Exception as e:
            logger.error(
                "handle_token_revoke_error",
                error=str(e),
                team_id=team_id
            )
            raise

    async def trigger_initial_indexing(
        self,
        team_id: str,
        db: AsyncSession
    ) -> None:
        """Trigger initial indexing job for newly installed workspace"""

        try:
            # Update workspace indexing status
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                logger.error("workspace_not_found_for_indexing", team_id=team_id)
                return

            workspace.indexing_status = "in_progress"
            workspace.indexing_started_at = datetime.utcnow()
            await db.commit()

            # Queue initial indexing job
            await queue_manager.publish(
                queue=queue_manager.PROCESSING_QUEUE,
                message={
                    "task": "initial_workspace_indexing",
                    "team_id": team_id,
                    "workspace_id": workspace.id,
                    "bot_token": workspace.bot_access_token
                },
                routing_key="workspace.indexing.initial"
            )

            logger.info(
                "initial_indexing_triggered",
                team_id=team_id,
                team_name=workspace.team_name
            )

        except Exception as e:
            logger.error(
                "trigger_indexing_error",
                error=str(e),
                team_id=team_id
            )
            raise


# Global installation handler instance
installation_handler = InstallationHandler()
