"""
Integration Orchestrator

Handles progressive integration of data sources (Slack, GitHub, etc.)
Manages indexing workflows, ensures data integrity, and builds cross-source graphs.

Key Features:
- Progressive integration (add sources over time)
- Resumable indexing (checkpoint/resume on failure)
- Background processing with progress tracking
- User notifications at key milestones
- Graceful degradation (bot works with partial data)
"""
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.logging import get_logger
from app.core.queue import queue_manager
from app.models.integration_status import IntegrationStatus, CrossSourceGraph, IndexingJob
from app.models.workspace import Workspace

logger = get_logger(__name__)


class IntegrationOrchestrator:
    """
    Orchestrates multi-source integration and indexing workflows

    Handles:
    1. Initial Slack-only installation
    2. Adding GitHub later
    3. Adding both simultaneously
    4. Incremental syncs
    5. Cross-source graph building
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ============================================================================
    # INSTALLATION WORKFLOWS
    # ============================================================================

    async def handle_slack_installation(
        self,
        team_id: str,
        bot_token: str,
        workspace: Workspace
    ) -> Dict[str, Any]:
        """
        Handle initial Slack app installation

        Flow:
        1. Create integration status (Slack)
        2. Queue Slack indexing job
        3. Queue vocabulary seeding
        4. Notify user that indexing started
        5. Return estimated completion time

        Args:
            team_id: Workspace team ID
            bot_token: Bot access token
            workspace: Workspace model

        Returns:
            Dict with job_id and estimated_time
        """
        try:
            logger.info("slack_installation_started", team_id=team_id)

            # Step 1: Create integration status for Slack
            slack_status = await self._get_or_create_integration_status(
                team_id=team_id,
                source_type="slack"
            )

            slack_status.is_connected = "true"
            slack_status.connected_at = datetime.utcnow()
            slack_status.indexing_status = "pending"

            await self.db.commit()

            # Step 2: Estimate workload
            # TODO: Get channel count from Slack API
            estimated_channels = workspace.total_channels_indexed or 50
            estimated_messages = estimated_channels * 1000  # Average 1k messages per channel
            estimated_minutes = max(5, estimated_messages // 10000)  # ~10k messages/minute

            slack_status.total_items = estimated_messages
            await self.db.commit()

            # Step 3: Queue Slack indexing job
            job_id = str(uuid.uuid4())
            await self._queue_indexing_job(
                job_id=job_id,
                team_id=team_id,
                source_type="slack",
                job_type="full_index",
                job_data={
                    "bot_token": bot_token,
                    "estimated_channels": estimated_channels,
                    "index_private": workspace.settings.get("privacy", {}).get("index_private_channels", True),
                    "index_archived": workspace.settings.get("privacy", {}).get("index_archived_channels", False)
                },
                priority=1  # Highest priority
            )

            # Step 4: Queue vocabulary seeding (parallel)
            vocab_job_id = str(uuid.uuid4())
            await queue_manager.publish(
                queue=queue_manager.PROCESSING_QUEUE,
                message={
                    "task": "seed_team_vocabulary",
                    "job_id": vocab_job_id,
                    "team_id": team_id
                },
                routing_key="vocabulary.seed"
            )

            # Step 5: Notify user
            await self._notify_user(
                team_id=team_id,
                message=f"🚀 *Indexing Started*\n\n"
                        f"We're indexing your Slack workspace history.\n\n"
                        f"• Estimated time: ~{estimated_minutes} minutes\n"
                        f"• You can start using the bot now with recent messages\n"
                        f"• I'll notify you when complete!\n\n"
                        f"Try: `/ask what's happening in #general?`"
            )

            logger.info(
                "slack_indexing_queued",
                team_id=team_id,
                job_id=job_id,
                estimated_messages=estimated_messages,
                estimated_minutes=estimated_minutes
            )

            return {
                "status": "indexing_started",
                "job_id": job_id,
                "estimated_minutes": estimated_minutes,
                "source_type": "slack",
                "can_use_bot": True,  # Bot works with partial data
                "progress_url": f"/api/integration/status/{team_id}"
            }

        except Exception as e:
            logger.error("slack_installation_error", error=str(e), team_id=team_id)
            raise

    async def handle_github_connection(
        self,
        team_id: str,
        github_access_token: str,
        selected_repos: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Handle GitHub integration (can happen anytime after Slack)

        Flow:
        1. Create integration status (GitHub)
        2. Queue GitHub indexing job
        3. Wait for Slack to complete (if still indexing)
        4. Queue cross-source graph building
        5. Notify user

        Args:
            team_id: Workspace team ID
            github_access_token: GitHub OAuth token
            selected_repos: Optional list of repos to index (default: all accessible)

        Returns:
            Dict with job_id and status
        """
        try:
            logger.info("github_connection_started", team_id=team_id)

            # Step 1: Check if Slack is indexed
            slack_status = await self._get_integration_status(team_id, "slack")

            if not slack_status:
                raise ValueError("Slack must be installed first")

            slack_complete = slack_status.indexing_status == "complete"

            # Step 2: Create GitHub integration status
            github_status = await self._get_or_create_integration_status(
                team_id=team_id,
                source_type="github"
            )

            github_status.is_connected = "true"
            github_status.connected_at = datetime.utcnow()
            github_status.indexing_status = "pending"
            github_status.config = {
                "selected_repos": selected_repos or [],
                "sync_frequency_hours": 24
            }

            await self.db.commit()

            # Step 3: Estimate workload
            estimated_repos = len(selected_repos) if selected_repos else 10
            estimated_files = estimated_repos * 500  # Average 500 files per repo
            estimated_minutes = max(10, estimated_files // 1000)  # ~1k files/minute

            github_status.total_items = estimated_files
            await self.db.commit()

            # Step 4: Queue GitHub indexing job
            job_id = str(uuid.uuid4())
            await self._queue_indexing_job(
                job_id=job_id,
                team_id=team_id,
                source_type="github",
                job_type="full_index",
                job_data={
                    "github_token": github_access_token,
                    "selected_repos": selected_repos,
                    "estimated_repos": estimated_repos
                },
                priority=2  # High priority
            )

            # Step 5: Queue graph building (if Slack is complete)
            if slack_complete:
                await self._queue_graph_building(team_id, priority=3)

            # Step 6: Notify user
            if slack_complete:
                message = (
                    f"✓ *GitHub Connected*\n\n"
                    f"Indexing your repositories in the background.\n\n"
                    f"• Estimated time: ~{estimated_minutes} minutes\n"
                    f"• Bot continues working with Slack data\n"
                    f"• Cross-source intelligence coming soon!\n\n"
                    f"Repos: {', '.join(selected_repos[:3]) if selected_repos else 'All accessible'}"
                    f"{'...' if selected_repos and len(selected_repos) > 3 else ''}"
                )
            else:
                message = (
                    f"✓ *GitHub Connected*\n\n"
                    f"Both Slack and GitHub are indexing.\n\n"
                    f"• Slack: {slack_status.progress_percentage:.0f}% complete\n"
                    f"• GitHub: Starting...\n"
                    f"• I'll notify you when both are ready!"
                )

            await self._notify_user(team_id, message)

            logger.info(
                "github_indexing_queued",
                team_id=team_id,
                job_id=job_id,
                slack_complete=slack_complete,
                estimated_repos=estimated_repos
            )

            return {
                "status": "indexing_started",
                "job_id": job_id,
                "estimated_minutes": estimated_minutes,
                "source_type": "github",
                "slack_complete": slack_complete,
                "graph_building_queued": slack_complete,
                "progress_url": f"/api/integration/status/{team_id}"
            }

        except Exception as e:
            logger.error("github_connection_error", error=str(e), team_id=team_id)
            raise

    # ============================================================================
    # INDEXING COMPLETION HANDLERS
    # ============================================================================

    async def handle_indexing_complete(
        self,
        team_id: str,
        source_type: str,
        result: Dict[str, Any]
    ) -> None:
        """
        Handle completion of source indexing

        Checks if cross-source graph building should start.

        Args:
            team_id: Workspace team ID
            source_type: 'slack' or 'github'
            result: Indexing results (counts, errors, etc.)
        """
        try:
            # Update integration status
            status = await self._get_integration_status(team_id, source_type)

            if not status:
                logger.error("integration_status_not_found", team_id=team_id, source_type=source_type)
                return

            status.indexing_status = "complete"
            status.indexing_completed_at = datetime.utcnow()
            status.last_full_sync = datetime.utcnow()
            status.items_indexed = result.get("items_indexed", 0)
            status.entities_extracted = result.get("entities_extracted", 0)
            status.progress_percentage = 100.0

            await self.db.commit()

            # Check if all sources are indexed
            all_statuses = await self._get_all_integration_statuses(team_id)
            all_complete = all(
                s.indexing_status == "complete"
                for s in all_statuses
                if s.is_connected == "true"
            )

            # If all sources complete, queue graph building
            if all_complete:
                graph_status = await self._get_or_create_graph_status(team_id)

                if graph_status.graph_status != "complete":
                    await self._queue_graph_building(team_id, priority=2)

            # Notify user
            if source_type == "slack":
                if all_complete:
                    message = (
                        f"✓ *All Sources Indexed!*\n\n"
                        f"Building cross-source intelligence graph.\n\n"
                        f"• Slack: {status.messages_indexed:,} messages\n"
                        f"• Bot is fully ready to use!\n"
                        f"• Advanced features activating...\n\n"
                        f"Try: `/ask show me code related to authentication`"
                    )
                else:
                    message = (
                        f"✓ *Slack Indexing Complete!*\n\n"
                        f"Your bot is ready to answer questions!\n\n"
                        f"• Indexed: {status.messages_indexed:,} messages\n"
                        f"• Channels: {result.get('channels_indexed', 0)}\n"
                        f"• Users: {result.get('users_synced', 0)}\n\n"
                        f"Try: `/ask what's the latest in #general?`"
                    )
            elif source_type == "github":
                message = (
                    f"✓ *GitHub Indexing Complete!*\n\n"
                    f"Code intelligence is now active!\n\n"
                    f"• Repos: {result.get('repos_indexed', 0)}\n"
                    f"• Code files: {result.get('files_indexed', 0):,}\n"
                    f"• Building Slack ↔ GitHub connections...\n\n"
                    f"Try: `/ask find PRs related to authentication bug`"
                )

            await self._notify_user(team_id, message)

            logger.info(
                "indexing_complete",
                team_id=team_id,
                source_type=source_type,
                items_indexed=status.items_indexed,
                all_complete=all_complete
            )

        except Exception as e:
            logger.error("handle_indexing_complete_error", error=str(e))

    async def handle_graph_building_complete(
        self,
        team_id: str,
        result: Dict[str, Any]
    ) -> None:
        """
        Handle completion of cross-source graph building

        Args:
            team_id: Workspace team ID
            result: Graph building results
        """
        try:
            graph_status = await self._get_or_create_graph_status(team_id)

            graph_status.graph_status = "complete"
            graph_status.graph_building_completed_at = datetime.utcnow()
            graph_status.last_full_rebuild = datetime.utcnow()
            graph_status.edges_built = result.get("edges_built", 0)
            graph_status.slack_to_github_edges = result.get("slack_to_github", 0)
            graph_status.github_to_slack_edges = result.get("github_to_slack", 0)
            graph_status.user_identity_links = result.get("user_links", 0)

            await self.db.commit()

            # Notify user
            message = (
                f"🎉 *Full Integration Complete!*\n\n"
                f"Your intelligent Slack bot is fully powered up!\n\n"
                f"✓ Slack history indexed\n"
                f"✓ GitHub repositories indexed\n"
                f"✓ Cross-source connections built\n\n"
                f"New capabilities:\n"
                f"• Find code discussions from Slack\n"
                f"• Link GitHub PRs to team conversations\n"
                f"• Track who's working on what\n\n"
                f"Try: `/ask show conversations about the auth PR`"
            )

            await self._notify_user(team_id, message)

            logger.info(
                "graph_building_complete",
                team_id=team_id,
                edges_built=graph_status.edges_built
            )

        except Exception as e:
            logger.error("handle_graph_building_complete_error", error=str(e))

    # ============================================================================
    # STATUS CHECKING
    # ============================================================================

    async def get_integration_overview(
        self,
        team_id: str
    ) -> Dict[str, Any]:
        """
        Get complete integration status overview

        Returns status of all sources and cross-source graph

        Args:
            team_id: Workspace team ID

        Returns:
            Dict with complete status
        """
        try:
            # Get all integration statuses
            statuses = await self._get_all_integration_statuses(team_id)

            # Get graph status
            graph_status = await self._get_or_create_graph_status(team_id)

            # Get active jobs
            active_jobs = await self._get_active_jobs(team_id)

            # Calculate overall readiness
            connected_sources = [s for s in statuses if s.is_connected == "true"]
            complete_sources = [s for s in connected_sources if s.indexing_status == "complete"]

            is_fully_ready = (
                len(connected_sources) == len(complete_sources) and
                len(complete_sources) > 0 and
                (graph_status.graph_status == "complete" if len(complete_sources) > 1 else True)
            )

            is_partially_ready = any(s.indexing_status == "complete" for s in statuses)

            return {
                "team_id": team_id,
                "is_fully_ready": is_fully_ready,
                "is_partially_ready": is_partially_ready,
                "bot_usable": is_partially_ready,  # Bot works with partial data
                "sources": {
                    s.source_type: s.to_dict() for s in statuses
                },
                "cross_source_graph": {
                    "status": graph_status.graph_status,
                    "edges_built": graph_status.edges_built,
                    "slack_to_github": graph_status.slack_to_github_edges,
                    "github_to_slack": graph_status.github_to_slack_edges,
                    "user_links": graph_status.user_identity_links,
                } if len(complete_sources) > 1 else None,
                "active_jobs": [j.to_dict() for j in active_jobs],
                "overall_progress": self._calculate_overall_progress(statuses, graph_status)
            }

        except Exception as e:
            logger.error("get_integration_overview_error", error=str(e))
            raise

    # ============================================================================
    # HELPER METHODS
    # ============================================================================

    async def _get_or_create_integration_status(
        self,
        team_id: str,
        source_type: str
    ) -> IntegrationStatus:
        """Get or create integration status for a source"""
        result = await self.db.execute(
            select(IntegrationStatus).where(
                and_(
                    IntegrationStatus.team_id == team_id,
                    IntegrationStatus.source_type == source_type
                )
            )
        )
        status = result.scalar_one_or_none()

        if not status:
            status = IntegrationStatus(
                team_id=team_id,
                source_type=source_type
            )
            self.db.add(status)
            await self.db.commit()
            await self.db.refresh(status)

        return status

    async def _get_integration_status(
        self,
        team_id: str,
        source_type: str
    ) -> Optional[IntegrationStatus]:
        """Get integration status"""
        result = await self.db.execute(
            select(IntegrationStatus).where(
                and_(
                    IntegrationStatus.team_id == team_id,
                    IntegrationStatus.source_type == source_type
                )
            )
        )
        return result.scalar_one_or_none()

    async def _get_all_integration_statuses(
        self,
        team_id: str
    ) -> List[IntegrationStatus]:
        """Get all integration statuses for team"""
        result = await self.db.execute(
            select(IntegrationStatus).where(IntegrationStatus.team_id == team_id)
        )
        return list(result.scalars().all())

    async def _get_or_create_graph_status(
        self,
        team_id: str
    ) -> CrossSourceGraph:
        """Get or create graph status"""
        result = await self.db.execute(
            select(CrossSourceGraph).where(CrossSourceGraph.team_id == team_id)
        )
        status = result.scalar_one_or_none()

        if not status:
            status = CrossSourceGraph(team_id=team_id)
            self.db.add(status)
            await self.db.commit()
            await self.db.refresh(status)

        return status

    async def _get_active_jobs(
        self,
        team_id: str
    ) -> List[IndexingJob]:
        """Get active indexing jobs"""
        result = await self.db.execute(
            select(IndexingJob).where(
                and_(
                    IndexingJob.team_id == team_id,
                    IndexingJob.status.in_(["queued", "running"])
                )
            )
        )
        return list(result.scalars().all())

    async def _queue_indexing_job(
        self,
        job_id: str,
        team_id: str,
        source_type: str,
        job_type: str,
        job_data: Dict[str, Any],
        priority: int = 5
    ) -> None:
        """Queue an indexing job"""
        # Create job record
        job = IndexingJob(
            job_id=job_id,
            team_id=team_id,
            source_type=source_type,
            job_type=job_type,
            status="queued",
            priority=priority,
            job_data=job_data
        )
        self.db.add(job)
        await self.db.commit()

        # Publish to queue
        await queue_manager.publish(
            queue=queue_manager.PROCESSING_QUEUE,
            message={
                "task": f"index_{source_type}",
                "job_id": job_id,
                "team_id": team_id,
                "job_type": job_type,
                "job_data": job_data,
                "priority": priority
            },
            routing_key=f"{source_type}.index"
        )

    async def _queue_graph_building(
        self,
        team_id: str,
        priority: int = 3
    ) -> None:
        """Queue cross-source graph building"""
        graph_status = await self._get_or_create_graph_status(team_id)

        graph_status.graph_status = "pending"
        await self.db.commit()

        job_id = str(uuid.uuid4())

        await queue_manager.publish(
            queue=queue_manager.PROCESSING_QUEUE,
            message={
                "task": "build_cross_source_graph",
                "job_id": job_id,
                "team_id": team_id,
                "priority": priority
            },
            routing_key="graph.build"
        )

        logger.info("graph_building_queued", team_id=team_id, job_id=job_id)

    async def _notify_user(
        self,
        team_id: str,
        message: str
    ) -> None:
        """Send notification to user (Slack DM)"""
        try:
            from app.services.notification_service import notification_service

            # Send notification directly
            await notification_service.send_integration_notification(
                team_id=team_id,
                message=message,
                db=self.db
            )

        except Exception as e:
            logger.error("notify_user_error", error=str(e), team_id=team_id)

    def _calculate_overall_progress(
        self,
        statuses: List[IntegrationStatus],
        graph_status: CrossSourceGraph
    ) -> Dict[str, Any]:
        """Calculate overall integration progress"""
        connected = [s for s in statuses if s.is_connected == "true"]

        if not connected:
            return {"percentage": 0, "phase": "not_started"}

        # Phase weights
        indexing_weight = 0.8
        graph_weight = 0.2

        # Calculate indexing progress
        indexing_progress = sum(s.progress_percentage for s in connected) / len(connected)

        # Calculate graph progress
        if len(connected) > 1:
            if graph_status.graph_status == "complete":
                graph_progress = 100.0
            elif graph_status.graph_status == "in_progress":
                graph_progress = (graph_status.edges_built / graph_status.total_edges_to_build * 100) if graph_status.total_edges_to_build > 0 else 0
            else:
                graph_progress = 0
        else:
            graph_progress = 100.0  # No graph needed for single source

        # Overall progress
        overall = (indexing_progress * indexing_weight) + (graph_progress * graph_weight)

        # Determine phase
        if overall < 10:
            phase = "starting"
        elif overall < 80:
            phase = "indexing"
        elif overall < 100:
            phase = "building_graph"
        else:
            phase = "complete"

        return {
            "percentage": round(overall, 1),
            "phase": phase,
            "indexing_progress": round(indexing_progress, 1),
            "graph_progress": round(graph_progress, 1) if len(connected) > 1 else None
        }


# Global instance factory
def get_integration_orchestrator(db: AsyncSession) -> IntegrationOrchestrator:
    """Get integration orchestrator instance"""
    return IntegrationOrchestrator(db)
