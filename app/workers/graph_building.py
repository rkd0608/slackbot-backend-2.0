"""Cross-Source Graph Building Worker

Builds relationships between Slack and GitHub content after both sources are indexed.
"""
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.database import db_manager
from app.models.integration_status import IntegrationStatus, CrossSourceGraph
from app.models.cross_source_node import CrossSourceNode
from app.models.cross_source_edge import CrossSourceEdge
from app.services.cross_source_link_detector import CrossSourceLinkDetector

logger = get_logger(__name__)

# Configuration
BATCH_SIZE = 50  # Process nodes in batches
CHECKPOINT_INTERVAL = 100  # Save checkpoint every 100 nodes processed


class GraphBuildingWorker:
    """
    Builds cross-source knowledge graph after integration sources are indexed

    Process:
    1. Check all sources are indexed
    2. Fetch all Slack and GitHub nodes
    3. Run link detection on each node
    4. Update CrossSourceGraph status
    5. Notify orchestrator on completion
    """

    async def build_graph(self, team_id: str) -> Dict[str, Any]:
        """
        Build cross-source knowledge graph for a team

        Args:
            team_id: Workspace team ID

        Returns:
            Statistics about graph building
        """
        logger.info("graph_building_started", team_id=team_id)

        async for db in db_manager.get_session():
            try:
                # Get or create CrossSourceGraph status
                stmt = select(CrossSourceGraph).where(
                    CrossSourceGraph.team_id == team_id
                )
                result = await db.execute(stmt)
                graph_status = result.scalar_one_or_none()

                if not graph_status:
                    # Create graph status if it doesn't exist
                    graph_status = CrossSourceGraph(
                        team_id=team_id,
                        graph_status="in_progress",
                        graph_building_started_at=datetime.utcnow(),
                        total_edges_to_build=0,
                        edges_built=0,
                        created_at=datetime.utcnow()
                    )
                    db.add(graph_status)
                else:
                    # Update to in_progress
                    graph_status.graph_status = "in_progress"
                    graph_status.graph_building_started_at = datetime.utcnow()

                await db.commit()

                # Verify all required sources are indexed
                sources_ready = await self._verify_sources_ready(team_id, db)
                if not sources_ready:
                    raise Exception("Not all sources are ready for graph building")

                # Get all nodes for this team
                node_counts = await self._count_nodes_by_source(team_id, db)
                total_nodes = sum(node_counts.values())

                logger.info(
                    "graph_building_nodes_counted",
                    team_id=team_id,
                    total_nodes=total_nodes,
                    by_source=node_counts
                )

                # Update estimated total edges (rough estimate: 30% of nodes will have cross-source links)
                estimated_edges = int(total_nodes * 0.3)
                graph_status.total_edges_to_build = estimated_edges
                await db.commit()

                # Initialize link detector
                link_detector = CrossSourceLinkDetector(db)

                # Process nodes in batches
                stats = await self._process_nodes_in_batches(
                    team_id=team_id,
                    link_detector=link_detector,
                    graph_status=graph_status,
                    db=db
                )

                # Count final edge statistics
                final_stats = await self._count_edges_by_type(team_id, db)

                # Update graph status to complete
                graph_status.graph_status = "complete"
                graph_status.graph_building_completed_at = datetime.utcnow()
                graph_status.edges_built = final_stats["total_edges"]
                graph_status.slack_to_github_edges = final_stats["slack_to_github"]
                graph_status.github_to_slack_edges = final_stats["github_to_slack"]
                graph_status.user_identity_links = final_stats["user_identity"]
                graph_status.last_full_rebuild = datetime.utcnow()
                await db.commit()

                logger.info(
                    "graph_building_completed",
                    team_id=team_id,
                    nodes_processed=stats["nodes_processed"],
                    edges_created=stats["edges_created"],
                    final_stats=final_stats
                )

                # Notify orchestrator about completion
                try:
                    from app.services.integration_orchestrator import get_integration_orchestrator
                    orchestrator = get_integration_orchestrator(db)

                    await orchestrator.handle_graph_building_complete(
                        team_id=team_id,
                        result={
                            "success": True,
                            "nodes_processed": stats["nodes_processed"],
                            "edges_built": final_stats["total_edges"],
                            "slack_to_github": final_stats["slack_to_github"],
                            "github_to_slack": final_stats["github_to_slack"],
                            "user_links": final_stats["user_identity"]
                        }
                    )

                    logger.info(
                        "orchestrator_notified_graph_complete",
                        team_id=team_id
                    )

                except Exception as e:
                    logger.error(
                        "orchestrator_notification_failed",
                        error=str(e),
                        team_id=team_id
                    )

                return {
                    "success": True,
                    "team_id": team_id,
                    "stats": stats,
                    "final_edge_counts": final_stats
                }

            except Exception as e:
                logger.error(
                    "graph_building_error",
                    error=str(e),
                    team_id=team_id
                )

                # Mark graph status as failed
                try:
                    stmt = select(CrossSourceGraph).where(
                        CrossSourceGraph.team_id == team_id
                    )
                    result = await db.execute(stmt)
                    failed_graph = result.scalar_one_or_none()

                    if failed_graph:
                        failed_graph.graph_status = "failed"
                        await db.commit()

                except Exception as db_error:
                    logger.error(
                        "failed_to_update_graph_status",
                        error=str(db_error),
                        team_id=team_id
                    )

                raise

    async def _verify_sources_ready(
        self,
        team_id: str,
        db: AsyncSession
    ) -> bool:
        """Verify that required sources are indexed"""
        try:
            # Check Slack status
            slack_result = await db.execute(
                select(IntegrationStatus).where(
                    and_(
                        IntegrationStatus.team_id == team_id,
                        IntegrationStatus.source_type == "slack"
                    )
                )
            )
            slack_status = slack_result.scalar_one_or_none()

            if not slack_status or slack_status.indexing_status != "complete":
                logger.error(
                    "slack_not_ready_for_graph",
                    team_id=team_id,
                    status=slack_status.indexing_status if slack_status else "missing"
                )
                return False

            # GitHub is optional, but if connected, must be complete
            github_result = await db.execute(
                select(IntegrationStatus).where(
                    and_(
                        IntegrationStatus.team_id == team_id,
                        IntegrationStatus.source_type == "github"
                    )
                )
            )
            github_status = github_result.scalar_one_or_none()

            if github_status:
                if github_status.indexing_status != "complete":
                    logger.error(
                        "github_not_ready_for_graph",
                        team_id=team_id,
                        status=github_status.indexing_status
                    )
                    return False

            logger.info(
                "sources_verified_for_graph",
                team_id=team_id,
                slack_ready=True,
                github_ready=github_status is not None
            )

            return True

        except Exception as e:
            logger.error(
                "source_verification_failed",
                error=str(e),
                team_id=team_id
            )
            return False

    async def _count_nodes_by_source(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Dict[str, int]:
        """Count nodes by source type"""
        try:
            result = await db.execute(
                select(
                    CrossSourceNode.source,
                    func.count(CrossSourceNode.id).label('count')
                ).where(
                    and_(
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.is_deleted == "false"
                    )
                ).group_by(CrossSourceNode.source)
            )

            counts = {row.source: row.count for row in result.all()}
            return counts

        except Exception as e:
            logger.error(
                "count_nodes_failed",
                error=str(e),
                team_id=team_id
            )
            return {}

    async def _process_nodes_in_batches(
        self,
        team_id: str,
        link_detector: CrossSourceLinkDetector,
        graph_status: CrossSourceGraph,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Process nodes in batches to build links"""
        try:
            stats = {
                "nodes_processed": 0,
                "edges_created": 0,
                "by_link_type": {
                    "explicit": 0,
                    "semantic": 0,
                    "person": 0,
                    "project": 0,
                    "temporal": 0
                }
            }

            offset = 0
            checkpoint_counter = 0

            while True:
                # Fetch batch of nodes
                result = await db.execute(
                    select(CrossSourceNode).where(
                        and_(
                            CrossSourceNode.team_id == team_id,
                            CrossSourceNode.is_deleted == "false"
                        )
                    ).order_by(CrossSourceNode.created_at.asc())
                    .limit(BATCH_SIZE)
                    .offset(offset)
                )
                nodes = result.scalars().all()

                if not nodes:
                    break  # No more nodes

                # Process each node in batch
                for node in nodes:
                    try:
                        node_stats = await link_detector.detect_all_links_for_node(
                            node=node,
                            team_id=team_id
                        )

                        stats["nodes_processed"] += 1
                        stats["edges_created"] += node_stats.get("total", 0)

                        # Update by type
                        for link_type, count in node_stats.items():
                            if link_type in stats["by_link_type"]:
                                stats["by_link_type"][link_type] += count

                        checkpoint_counter += 1

                        # Save checkpoint periodically
                        if checkpoint_counter >= CHECKPOINT_INTERVAL:
                            graph_status.edges_built = stats["edges_created"]

                            # Update progress percentage
                            if graph_status.total_edges_to_build > 0:
                                progress = min(
                                    100.0,
                                    (stats["edges_created"] / graph_status.total_edges_to_build) * 100
                                )
                                # Store in metadata or use a new field if added
                                logger.info(
                                    "graph_building_checkpoint",
                                    team_id=team_id,
                                    nodes_processed=stats["nodes_processed"],
                                    edges_created=stats["edges_created"],
                                    progress=progress
                                )

                            await db.commit()
                            checkpoint_counter = 0

                    except Exception as e:
                        logger.error(
                            "node_link_detection_failed",
                            node_id=node.canonical_id,
                            error=str(e)
                        )
                        continue

                offset += BATCH_SIZE

                # Rate limiting
                await asyncio.sleep(0.5)

            # Final commit
            await db.commit()

            return stats

        except Exception as e:
            logger.error(
                "batch_processing_failed",
                error=str(e),
                team_id=team_id
            )
            raise

    async def _count_edges_by_type(
        self,
        team_id: str,
        db: AsyncSession
    ) -> Dict[str, int]:
        """Count edges by direction and type"""
        try:
            # Count all edges
            total_result = await db.execute(
                select(func.count(CrossSourceEdge.id)).where(
                    CrossSourceEdge.team_id == team_id
                )
            )
            total_edges = total_result.scalar() or 0

            # Count Slack -> GitHub edges
            # (source_node from slack, target_node from github)
            slack_to_github_result = await db.execute(
                select(func.count(CrossSourceEdge.id)).where(
                    and_(
                        CrossSourceEdge.team_id == team_id,
                        CrossSourceEdge.source_node_id.like("slack:%"),
                        CrossSourceEdge.target_node_id.like("github:%")
                    )
                )
            )
            slack_to_github = slack_to_github_result.scalar() or 0

            # Count GitHub -> Slack edges
            github_to_slack_result = await db.execute(
                select(func.count(CrossSourceEdge.id)).where(
                    and_(
                        CrossSourceEdge.team_id == team_id,
                        CrossSourceEdge.source_node_id.like("github:%"),
                        CrossSourceEdge.target_node_id.like("slack:%")
                    )
                )
            )
            github_to_slack = github_to_slack_result.scalar() or 0

            # Count user identity links
            # (person entity to user nodes)
            user_identity_result = await db.execute(
                select(func.count(CrossSourceEdge.id)).where(
                    and_(
                        CrossSourceEdge.team_id == team_id,
                        CrossSourceEdge.source_node_id.like("entity:person:%")
                    )
                )
            )
            user_identity = user_identity_result.scalar() or 0

            return {
                "total_edges": total_edges,
                "slack_to_github": slack_to_github,
                "github_to_slack": github_to_slack,
                "user_identity": user_identity
            }

        except Exception as e:
            logger.error(
                "count_edges_failed",
                error=str(e),
                team_id=team_id
            )
            return {
                "total_edges": 0,
                "slack_to_github": 0,
                "github_to_slack": 0,
                "user_identity": 0
            }


# Global worker instance
graph_building_worker = GraphBuildingWorker()
