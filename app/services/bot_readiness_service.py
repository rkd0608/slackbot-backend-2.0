"""
Bot Readiness Service

Determines when the bot is ready to answer queries based on integration status.

Rules:
- Bot ONLY available when 100% indexed
- Slack-only: Needs Slack complete
- Slack+GitHub: Needs both complete AND graph built
- Late GitHub addition: Bot stays available for Slack queries, full features after GitHub+graph complete
"""
from typing import Dict, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.logging import get_logger
from app.models.integration_status import IntegrationStatus, CrossSourceGraph

logger = get_logger(__name__)


class BotReadinessService:
    """
    Checks if bot is ready to answer queries

    Provides:
    - is_bot_ready(): Overall readiness
    - get_available_features(): What features work now
    - get_readiness_message(): Message to show user if not ready
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def is_bot_ready(
        self,
        team_id: str,
        query_type: Optional[str] = None
    ) -> bool:
        """
        Check if bot is ready to answer queries

        Args:
            team_id: Workspace team ID
            query_type: Optional specific query type to check
                       ('slack_search', 'code_search', 'cross_source')
                       If None, checks overall readiness

        Returns:
            True if bot can answer this type of query
        """
        try:
            # Get integration statuses
            slack_status = await self._get_status(team_id, "slack")

            if not slack_status or slack_status.indexing_status != "complete":
                return False  # Slack must be complete

            # If query type not specified or is Slack-only, Slack complete is enough
            if query_type is None or query_type == "slack_search":
                return True

            # For code/cross-source queries, need GitHub
            github_status = await self._get_status(team_id, "github")

            if query_type == "code_search":
                # Code search needs GitHub indexed
                return (
                    github_status is not None and
                    github_status.is_connected == "true" and
                    github_status.indexing_status == "complete"
                )

            if query_type == "cross_source":
                # Cross-source needs both complete + graph built
                if not github_status or github_status.indexing_status != "complete":
                    return False

                graph_status = await self._get_graph_status(team_id)
                return (
                    graph_status is not None and
                    graph_status.graph_status == "complete"
                )

            return True

        except Exception as e:
            logger.error("is_bot_ready_error", error=str(e), team_id=team_id)
            return False

    async def get_readiness_status(
        self,
        team_id: str
    ) -> Dict[str, Any]:
        """
        Get comprehensive readiness status

        Returns:
            Dict with:
            - is_ready: bool
            - available_features: list
            - unavailable_features: list
            - status_message: str
            - setup_progress: dict
        """
        try:
            slack_status = await self._get_status(team_id, "slack")
            github_status = await self._get_status(team_id, "github")
            graph_status = await self._get_graph_status(team_id)

            # Determine overall readiness
            slack_ready = slack_status and slack_status.indexing_status == "complete"
            github_connected = github_status and github_status.is_connected == "true"
            github_ready = github_connected and github_status.indexing_status == "complete"
            graph_ready = graph_status and graph_status.graph_status == "complete"

            # Determine what's available
            available_features = []
            unavailable_features = []

            if slack_ready:
                available_features.extend([
                    "Slack message search",
                    "Team discussions",
                    "Entity extraction",
                    "Daily digests",
                    "Conversation context"
                ])
            else:
                unavailable_features.extend([
                    "Slack message search",
                    "Team discussions"
                ])

            if github_ready:
                available_features.extend([
                    "Code search",
                    "Repository browsing",
                    "Code intelligence"
                ])
            elif github_connected:
                unavailable_features.extend([
                    "Code search (indexing...)",
                    "Repository browsing (indexing...)"
                ])

            if github_ready and graph_ready:
                available_features.extend([
                    "Cross-source queries",
                    "Link Slack to GitHub",
                    "Find code from discussions"
                ])
            elif github_connected:
                unavailable_features.extend([
                    "Cross-source queries (building...)",
                    "Link Slack to GitHub (building...)"
                ])

            # Overall readiness
            is_ready = slack_ready

            # Construct status message
            if not slack_ready:
                status_message = "🔄 Setting up your workspace... You'll be notified when ready!"
                phase = "indexing_slack"
            elif slack_ready and not github_connected:
                status_message = "✅ Your bot is ready! Ask questions about your Slack workspace."
                phase = "slack_only_ready"
            elif slack_ready and github_connected and not github_ready:
                status_message = "✅ Bot ready for Slack queries. Code search coming soon!"
                phase = "github_indexing"
            elif github_ready and not graph_ready:
                status_message = "✅ Bot ready! Building cross-source intelligence..."
                phase = "graph_building"
            else:
                status_message = "🎉 Bot fully ready with all features!"
                phase = "fully_ready"

            # Setup progress
            setup_progress = {}

            if slack_status:
                setup_progress["slack"] = {
                    "status": slack_status.indexing_status,
                    "progress": slack_status.progress_percentage,
                    "messages_indexed": slack_status.messages_indexed,
                    "complete": slack_ready
                }

            if github_status:
                setup_progress["github"] = {
                    "status": github_status.indexing_status,
                    "progress": github_status.progress_percentage,
                    "files_indexed": github_status.files_indexed,
                    "complete": github_ready
                }

            if graph_status:
                progress_pct = 0
                if graph_status.total_edges_to_build > 0:
                    progress_pct = (graph_status.edges_built / graph_status.total_edges_to_build) * 100

                setup_progress["graph"] = {
                    "status": graph_status.graph_status,
                    "progress": progress_pct,
                    "edges_built": graph_status.edges_built,
                    "complete": graph_ready
                }

            return {
                "team_id": team_id,
                "is_ready": is_ready,
                "phase": phase,
                "available_features": available_features,
                "unavailable_features": unavailable_features,
                "status_message": status_message,
                "setup_progress": setup_progress
            }

        except Exception as e:
            logger.error("get_readiness_status_error", error=str(e), team_id=team_id)
            return {
                "team_id": team_id,
                "is_ready": False,
                "phase": "error",
                "available_features": [],
                "unavailable_features": [],
                "status_message": "Unable to determine bot status. Please contact support.",
                "setup_progress": {},
                "error": str(e)
            }

    async def get_not_ready_message(
        self,
        team_id: str
    ) -> str:
        """
        Get message to show user when bot is not ready

        Returns user-friendly message explaining status
        """
        try:
            status = await self.get_readiness_status(team_id)

            if status["is_ready"]:
                return None  # Bot is ready, no message needed

            phase = status["phase"]
            progress = status["setup_progress"]

            if phase == "indexing_slack":
                slack_progress = progress.get("slack", {})
                pct = slack_progress.get("progress", 0)
                return (
                    f"🔄 **Setting up your workspace**\n\n"
                    f"Indexing Slack history: {pct:.0f}% complete\n"
                    f"You'll be notified when ready!\n\n"
                    f"*Estimated time: {self._estimate_time_remaining(pct)} minutes*"
                )

            elif phase == "github_indexing":
                github_progress = progress.get("github", {})
                pct = github_progress.get("progress", 0)
                return (
                    f"⚡ **GitHub integration in progress**\n\n"
                    f"Your bot is ready for Slack questions!\n"
                    f"Code search: {pct:.0f}% indexed\n\n"
                    f"*Full features coming soon!*"
                )

            elif phase == "graph_building":
                graph_progress = progress.get("graph", {})
                pct = graph_progress.get("progress", 0)
                return (
                    f"🔗 **Building cross-source intelligence**\n\n"
                    f"Your bot is fully functional!\n"
                    f"Advanced features: {pct:.0f}% ready\n\n"
                    f"*Cross-source queries coming soon!*"
                )

            else:
                return (
                    f"🔄 **Bot setup in progress**\n\n"
                    f"We're setting up your intelligent assistant.\n"
                    f"You'll be notified when ready!"
                )

        except Exception as e:
            logger.error("get_not_ready_message_error", error=str(e), team_id=team_id)
            return (
                "⚠️ **Setup Status Unknown**\n\n"
                "We're working on setting up your bot.\n"
                "If this persists, please contact support."
            )

    async def _get_status(
        self,
        team_id: str,
        source_type: str
    ) -> Optional[IntegrationStatus]:
        """Get integration status for a source"""
        result = await self.db.execute(
            select(IntegrationStatus).where(
                and_(
                    IntegrationStatus.team_id == team_id,
                    IntegrationStatus.source_type == source_type
                )
            )
        )
        return result.scalar_one_or_none()

    async def _get_graph_status(
        self,
        team_id: str
    ) -> Optional[CrossSourceGraph]:
        """Get cross-source graph status"""
        result = await self.db.execute(
            select(CrossSourceGraph).where(CrossSourceGraph.team_id == team_id)
        )
        return result.scalar_one_or_none()

    def _estimate_time_remaining(self, progress_percentage: float) -> int:
        """Estimate minutes remaining based on progress"""
        if progress_percentage >= 95:
            return 5
        elif progress_percentage >= 75:
            return 10
        elif progress_percentage >= 50:
            return 20
        elif progress_percentage >= 25:
            return 30
        else:
            return 45


# Factory function
def get_bot_readiness_service(db: AsyncSession) -> BotReadinessService:
    """Get bot readiness service instance"""
    return BotReadinessService(db)
