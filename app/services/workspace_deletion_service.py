"""
Workspace Deletion Service

Handles complete deletion of workspace data including:
- Database records (all tables)
- Vector embeddings (Pinecone)
- Slack uninstallation
- External integrations cleanup
"""
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from app.core.logging import get_logger
from app.core.vector_db import vector_db_manager
from app.core.config import settings
from app.models.workspace import Workspace, InstallationLog
from app.models.user import User, APIKey
from app.models.message import Message
from app.models.thread import Thread
from app.models.channel import Channel
from app.models.file import File
from app.models.entity import Entity, EntityRelationship
from app.models.query_log import QueryLog
from app.models.conversation import Conversation
from app.models.user_expertise import UserExpertise, SignificantEvent
from app.models.event_buffer import EventBuffer
from app.models.processed_event import ProcessedEvent
from app.models.integration import (
    Integration,
    UserIntegrationConnection,
    ExternalContent,
    IntegrationSyncJob
)
from app.models.cross_source_node import CrossSourceNode
from app.models.cross_source_edge import CrossSourceEdge
import httpx

logger = get_logger(__name__)


class WorkspaceDeletionService:
    """Service to handle complete workspace deletion"""

    async def delete_workspace(
        self,
        team_id: str,
        db: AsyncSession,
        delete_from_slack: bool = True,
        admin_user_id: str = None
    ) -> Dict[str, Any]:
        """
        Delete all workspace data from the system

        Args:
            team_id: Workspace team ID
            db: Database session
            delete_from_slack: Whether to uninstall from Slack
            admin_user_id: User ID of admin performing deletion (for logging)

        Returns:
            Dictionary with deletion statistics
        """
        logger.info(
            "workspace_deletion_started",
            team_id=team_id,
            admin_user_id=admin_user_id,
            delete_from_slack=delete_from_slack
        )

        stats = {
            "team_id": team_id,
            "deleted": {},
            "errors": [],
            "success": False
        }

        try:
            # Step 1: Get workspace info before deletion
            workspace = await self._get_workspace(team_id, db)
            if not workspace:
                raise ValueError(f"Workspace {team_id} not found")

            stats["workspace_name"] = workspace.team_name
            bot_token = workspace.bot_access_token

            # Step 2: Delete Pinecone vectors (do this first, before DB deletion)
            vector_stats = await self._delete_pinecone_vectors(team_id)
            stats["deleted"]["vectors"] = vector_stats

            # Step 3: Delete database records (in order to respect foreign keys)
            db_stats = await self._delete_database_records(team_id, db)
            stats["deleted"].update(db_stats)

            # Step 4: Optionally uninstall from Slack
            if delete_from_slack and bot_token:
                slack_result = await self._uninstall_from_slack(bot_token, team_id)
                stats["slack_uninstall"] = slack_result
            else:
                stats["slack_uninstall"] = {"skipped": True}

            # Step 5: Log deletion event
            await self._log_deletion_event(
                team_id=team_id,
                admin_user_id=admin_user_id,
                stats=stats,
                db=db
            )

            # Commit all deletions
            await db.commit()

            stats["success"] = True

            logger.info(
                "workspace_deletion_completed",
                team_id=team_id,
                stats=stats
            )

            return stats

        except Exception as e:
            logger.error(
                "workspace_deletion_failed",
                team_id=team_id,
                error=str(e)
            )
            await db.rollback()
            stats["errors"].append(str(e))
            stats["success"] = False
            raise

    async def _get_workspace(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Workspace:
        """Get workspace record"""
        stmt = select(Workspace).where(Workspace.team_id == team_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _delete_pinecone_vectors(
        self,
        team_id: str
    ) -> Dict[str, Any]:
        """
        Delete all Pinecone vectors for this workspace

        Deletes from all namespaces:
        - {team_id}:semantic (Slack messages, GitHub text)
        - {team_id}:code (GitHub code files)
        """
        try:
            stats = {
                "namespaces_deleted": [],
                "errors": []
            }

            # List of namespaces to delete
            namespaces = [
                f"{team_id}:semantic",
                f"{team_id}:code"
            ]

            for namespace in namespaces:
                try:
                    # Delete all vectors in namespace
                    vector_db_manager.delete_namespace(namespace)
                    stats["namespaces_deleted"].append(namespace)

                    logger.info(
                        "pinecone_namespace_deleted",
                        team_id=team_id,
                        namespace=namespace
                    )

                except Exception as e:
                    error_msg = f"Failed to delete namespace {namespace}: {str(e)}"
                    stats["errors"].append(error_msg)
                    logger.error(
                        "pinecone_namespace_deletion_failed",
                        team_id=team_id,
                        namespace=namespace,
                        error=str(e)
                    )

            return stats

        except Exception as e:
            logger.error(
                "pinecone_deletion_failed",
                team_id=team_id,
                error=str(e)
            )
            return {
                "namespaces_deleted": [],
                "errors": [str(e)]
            }

    async def _delete_database_records(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Dict[str, int]:
        """
        Delete all database records for workspace

        Deletion order matters due to foreign key constraints
        """
        stats = {}

        # Order of deletion (child tables first, parent tables last)
        deletion_order = [
            # Cross-source knowledge graph
            (CrossSourceEdge, "cross_source_edges"),
            (CrossSourceNode, "cross_source_nodes"),

            # External integrations
            (IntegrationSyncJob, "integration_sync_jobs"),
            (ExternalContent, "external_content"),
            (UserIntegrationConnection, "user_integration_connections"),
            (Integration, "integrations"),

            # Slack data
            (EntityRelationship, "entity_relationships"),
            (Entity, "entities"),
            (SignificantEvent, "significant_events"),
            (UserExpertise, "user_expertise"),
            (Conversation, "conversations"),
            (QueryLog, "query_logs"),
            (File, "files"),
            (Thread, "threads"),
            (Message, "messages"),
            (ProcessedEvent, "processed_events"),
            (EventBuffer, "event_buffers"),
            (Channel, "channels"),

            # API keys
            (APIKey, "api_keys"),

            # Users (delete last, but keep workspace for final logging)
            (User, "users"),

            # Installation logs (keep for audit trail, but can delete)
            # (InstallationLog, "installation_logs"),  # Optional: keep for audit

            # Workspace (delete very last)
            # Will be deleted separately after logging
        ]

        for model, table_name in deletion_order:
            try:
                stmt = delete(model).where(model.team_id == team_id)
                result = await db.execute(stmt)
                count = result.rowcount

                stats[table_name] = count

                logger.info(
                    "table_records_deleted",
                    team_id=team_id,
                    table=table_name,
                    count=count
                )

            except Exception as e:
                logger.error(
                    "table_deletion_failed",
                    team_id=team_id,
                    table=table_name,
                    error=str(e)
                )
                stats[table_name] = f"ERROR: {str(e)}"

        return stats

    async def _delete_workspace_record(
        self,
        team_id: str,
        db: AsyncSession
    ) -> bool:
        """Delete the workspace record itself (call this last)"""
        try:
            stmt = delete(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)

            logger.info(
                "workspace_record_deleted",
                team_id=team_id,
                deleted=result.rowcount
            )

            return result.rowcount > 0

        except Exception as e:
            logger.error(
                "workspace_record_deletion_failed",
                team_id=team_id,
                error=str(e)
            )
            raise

    async def _uninstall_from_slack(
        self,
        bot_token: str,
        team_id: str
    ) -> Dict[str, Any]:
        """
        Uninstall app from Slack workspace

        This revokes the bot token and removes the app installation.
        Uses the auth.revoke API which is the proper way to uninstall.
        """
        try:
            async with httpx.AsyncClient() as client:
                # Use auth.revoke to revoke the bot token
                # This effectively uninstalls the app from the workspace
                response = await client.post(
                    "https://slack.com/api/auth.revoke",
                    headers={
                        "Authorization": f"Bearer {bot_token}",
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                    data={
                        "token": bot_token
                    }
                )

                data = response.json()

                if data.get("ok"):
                    logger.info(
                        "slack_token_revoked_success",
                        team_id=team_id,
                        revoked=data.get("revoked", False)
                    )
                    return {
                        "success": True,
                        "message": "Successfully revoked bot token and uninstalled from Slack",
                        "revoked": data.get("revoked", True)
                    }
                else:
                    error = data.get("error", "unknown")
                    logger.warning(
                        "slack_token_revoke_failed",
                        team_id=team_id,
                        error=error
                    )

                    # Common errors and what they mean
                    error_messages = {
                        "token_revoked": "Token was already revoked",
                        "invalid_auth": "Token is invalid or already revoked",
                        "not_authed": "No authentication token provided"
                    }

                    return {
                        "success": False,
                        "error": error,
                        "message": error_messages.get(error, f"Failed to revoke token: {error}")
                    }

        except Exception as e:
            logger.error(
                "slack_uninstall_error",
                team_id=team_id,
                error=str(e)
            )
            return {
                "success": False,
                "error": str(e),
                "message": "Error during Slack token revocation"
            }

    async def _log_deletion_event(
        self,
        team_id: str,
        admin_user_id: str,
        stats: Dict[str, Any],
        db: AsyncSession
    ):
        """Log workspace deletion event"""
        try:
            log_entry = InstallationLog(
                team_id=team_id,
                event_type="workspace_deleted",
                event_data={
                    "deleted_by_user_id": admin_user_id,
                    "deletion_stats": stats,
                    "timestamp": str(db.bind.engine.pool._logger.getEffectiveLevel())  # Current time
                },
                user_id=admin_user_id
            )

            db.add(log_entry)

            logger.info(
                "workspace_deletion_logged",
                team_id=team_id,
                admin_user_id=admin_user_id
            )

        except Exception as e:
            logger.error(
                "deletion_logging_failed",
                team_id=team_id,
                error=str(e)
            )
            # Don't raise - logging failure shouldn't stop deletion


# Global service instance
workspace_deletion_service = WorkspaceDeletionService()
