"""
Enhanced Cross-Source Link Detector

Builds on existing link detection to add:
1. Semantic similarity-based linking
2. Person-to-code author linking
3. Project mention to repository linking
4. Temporal proximity linking
5. Batch processing for existing nodes
"""
import uuid
import re
from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc

from app.core.logging import get_logger
from app.models.cross_source_node import CrossSourceNode, NodeType
from app.models.cross_source_edge import CrossSourceEdge, EdgeType, DetectionMethod
from app.services.cross_source_link_service import get_link_service
from app.core.vector_db import vector_db_manager

logger = get_logger(__name__)


class CrossSourceLinkDetector:
    """
    Enhanced link detection across Slack, GitHub, and future sources

    Key capabilities:
    - Explicit link detection (URLs, #refs)
    - Semantic similarity linking
    - Person-to-author mapping
    - Project-to-repo mapping
    - Temporal co-mention detection
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.link_service = get_link_service(db)

    async def detect_all_links_for_node(
        self,
        node: CrossSourceNode,
        team_id: str
    ) -> Dict[str, Any]:
        """
        Run all link detection methods on a single node

        Args:
            node: Node to detect links from
            team_id: Workspace team ID

        Returns:
            Statistics about links created
        """
        stats = {
            'explicit': 0,
            'semantic': 0,
            'person': 0,
            'project': 0,
            'temporal': 0,
            'total': 0
        }

        try:
            # 1. Explicit links (already handled by adapters, but double-check)
            explicit_count = await self._detect_explicit_links(node, team_id)
            stats['explicit'] = explicit_count

            # 2. Semantic similarity links
            semantic_count = await self._detect_semantic_links(node, team_id)
            stats['semantic'] = semantic_count

            # 3. Person-to-author links
            if node.source == 'slack' and node.node_type == NodeType.MESSAGE:
                person_count = await self._detect_person_links(node, team_id)
                stats['person'] = person_count

            # 4. Project mention to repo links
            project_count = await self._detect_project_links(node, team_id)
            stats['project'] = project_count

            # 5. Temporal co-mention links
            temporal_count = await self._detect_temporal_links(node, team_id)
            stats['temporal'] = temporal_count

            stats['total'] = sum(v for k, v in stats.items() if k != 'total')

            await self.db.commit()

            logger.info(
                "link_detection_completed",
                node_id=node.canonical_id,
                stats=stats
            )

            return stats

        except Exception as e:
            logger.error(
                "link_detection_failed",
                node_id=node.canonical_id,
                error=str(e)
            )
            await self.db.rollback()
            return stats

    async def batch_detect_missing_links(
        self,
        team_id: str,
        source_filter: Optional[str] = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Process existing nodes that may not have links yet

        This is useful for:
        - Backfilling links after adding new detection methods
        - Processing nodes added before link detection was enabled
        - Rebuilding the graph after data changes

        Args:
            team_id: Workspace to process
            source_filter: Only process nodes from this source (e.g., "slack", "github")
            limit: Maximum nodes to process in this batch

        Returns:
            Batch processing statistics
        """
        try:
            logger.info(
                "batch_link_detection_started",
                team_id=team_id,
                source_filter=source_filter,
                limit=limit
            )

            # Find nodes with few or no outbound edges
            query = select(
                CrossSourceNode.canonical_id,
                func.count(CrossSourceEdge.id).label('edge_count')
            ).outerjoin(
                CrossSourceEdge,
                and_(
                    CrossSourceEdge.source_node_id == CrossSourceNode.canonical_id,
                    CrossSourceEdge.team_id == team_id
                )
            ).where(
                CrossSourceNode.team_id == team_id
            ).group_by(
                CrossSourceNode.canonical_id
            ).having(
                func.count(CrossSourceEdge.id) < 3  # Nodes with fewer than 3 edges
            ).order_by(
                CrossSourceNode.created_at.desc()
            ).limit(limit)

            if source_filter:
                query = query.where(CrossSourceNode.source == source_filter)

            result = await self.db.execute(query)
            candidates = result.all()

            batch_stats = {
                'nodes_processed': 0,
                'links_created': 0,
                'by_type': {
                    'explicit': 0,
                    'semantic': 0,
                    'person': 0,
                    'project': 0,
                    'temporal': 0
                }
            }

            for canonical_id, existing_edge_count in candidates:
                # Fetch full node
                node_result = await self.db.execute(
                    select(CrossSourceNode).where(
                        and_(
                            CrossSourceNode.canonical_id == canonical_id,
                            CrossSourceNode.team_id == team_id
                        )
                    )
                )
                node = node_result.scalar_one_or_none()

                if not node:
                    continue

                # Detect links
                node_stats = await self.detect_all_links_for_node(node, team_id)

                batch_stats['nodes_processed'] += 1
                batch_stats['links_created'] += node_stats['total']

                for link_type, count in node_stats.items():
                    if link_type in batch_stats['by_type']:
                        batch_stats['by_type'][link_type] += count

            logger.info(
                "batch_link_detection_completed",
                team_id=team_id,
                stats=batch_stats
            )

            return batch_stats

        except Exception as e:
            logger.error(
                "batch_link_detection_failed",
                team_id=team_id,
                error=str(e)
            )
            raise

    # ===================
    # DETECTION METHODS
    # ===================

    async def _detect_explicit_links(
        self,
        node: CrossSourceNode,
        team_id: str
    ) -> int:
        """
        Detect explicit URL-based links

        This uses the existing adapter's detect_outbound_links method
        """
        try:
            # Get appropriate adapter
            from app.integrations.registry import IntegrationRegistry
            from app.integrations.base_adapter import UnifiedDocument

            # Convert node to UnifiedDocument
            doc = UnifiedDocument(
                source=node.source,
                source_id=node.source_id,
                canonical_id=node.canonical_id,
                node_type=node.node_type,
                title=node.title,
                content=node.content or '',
                url=node.url,
                author=node.author,
                author_id=node.author_id,
                created_at=node.created_at,
                updated_at=node.updated_at,
                visibility=node.visibility,
                project_context=node.project_context,
                channel_context=node.channel_context,
                tags=node.tags or []
            )

            # Try to get adapter (may not have credentials, that's ok)
            try:
                adapter = IntegrationRegistry.get_adapter(
                    source_name=node.source,
                    team_id=team_id,
                    credentials={}
                )

                if adapter:
                    links = await adapter.detect_outbound_links(doc)

                    # Create edges for detected links
                    created = 0
                    for link in links:
                        target_id = link.get('canonical_id') or f"{link['target_source']}:{link['target_id']}"

                        # Check if target exists
                        target = await self._get_node(target_id, team_id)
                        if not target:
                            continue

                        # Check if edge exists
                        if await self._edge_exists(node.canonical_id, target_id, team_id):
                            continue

                        # Create edge
                        edge_type = link.get('edge_type', EdgeType.REFERENCES)
                        await self.link_service.create_edge(
                            source_node_id=node.canonical_id,
                            target_node_id=target_id,
                            edge_type=edge_type,
                            team_id=team_id,
                            confidence=link.get('confidence', 1.0),
                            detection_method=DetectionMethod.EXPLICIT_LINK,
                            evidence=link.get('evidence', ''),
                            link_text=link.get('link_text', '')
                        )
                        created += 1

                    return created

            except Exception as e:
                logger.debug("adapter_not_available", source=node.source, error=str(e))

            return 0

        except Exception as e:
            logger.error("explicit_link_detection_error", error=str(e))
            return 0

    async def _detect_semantic_links(
        self,
        node: CrossSourceNode,
        team_id: str,
        similarity_threshold: float = 0.75,
        max_links: int = 5
    ) -> int:
        """
        Detect semantically similar nodes across sources

        Uses vector similarity to find related content
        """
        try:
            if not node.vector_id_semantic:
                # No embedding yet
                return 0

            # Search for similar nodes from OTHER sources
            # This creates cross-source connections
            search_filter = {
                "team_id": team_id,
                "source": {"$ne": node.source}  # Different source
            }

            similar_results = vector_db_manager.search(
                index_name="semantic_search",
                query_embedding_id=node.vector_id_semantic,
                top_k=max_links,
                filter_dict=search_filter,
                score_threshold=similarity_threshold
            )

            created = 0
            for result in similar_results:
                target_id = result['metadata'].get('canonical_id')
                score = result['score']

                if not target_id or target_id == node.canonical_id:
                    continue

                # Check if edge exists
                if await self._edge_exists(node.canonical_id, target_id, team_id):
                    continue

                # Create semantic similarity edge
                await self.link_service.create_edge(
                    source_node_id=node.canonical_id,
                    target_node_id=target_id,
                    edge_type=EdgeType.RELATED_TO,
                    team_id=team_id,
                    confidence=score,
                    detection_method=DetectionMethod.SEMANTIC_SIMILARITY,
                    evidence=f"Vector similarity score: {score:.3f}",
                    edge_metadata={
                        "similarity_score": score,
                        "detection_method": "vector_search"
                    }
                )
                created += 1

            return created

        except Exception as e:
            logger.error("semantic_link_detection_error", error=str(e), node=node.canonical_id)
            return 0

    async def _detect_person_links(
        self,
        node: CrossSourceNode,
        team_id: str
    ) -> int:
        """
        Link Slack users to GitHub authors

        Matches based on:
        - Email addresses
        - Display names
        - Username similarity
        """
        try:
            # Get message author from Slack
            if not node.author_id:
                return 0

            author_name = node.author or ''
            author_email = node.source_metadata.get('user_email', '') if node.source_metadata else ''

            # Find GitHub nodes by this author
            # Match by name or email
            github_nodes = await self.db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.source == 'github',
                        or_(
                            CrossSourceNode.author.ilike(f'%{author_name}%'),
                            CrossSourceNode.source_metadata['author_email'].astext == author_email
                        )
                    )
                ).limit(10)
            )
            github_nodes = github_nodes.scalars().all()

            created = 0
            for github_node in github_nodes:
                # Check if edge exists
                if await self._edge_exists(node.canonical_id, github_node.canonical_id, team_id):
                    continue

                # Create person link
                await self.link_service.create_edge(
                    source_node_id=node.canonical_id,
                    target_node_id=github_node.canonical_id,
                    edge_type=EdgeType.AUTHORED_BY,
                    team_id=team_id,
                    confidence=0.8,
                    detection_method=DetectionMethod.AUTHOR_MATCH,
                    evidence=f"Same author: {author_name}",
                    edge_metadata={
                        "author_name": author_name,
                        "author_email": author_email
                    }
                )
                created += 1

            return created

        except Exception as e:
            logger.error("person_link_detection_error", error=str(e))
            return 0

    async def _detect_project_links(
        self,
        node: CrossSourceNode,
        team_id: str
    ) -> int:
        """
        Link project mentions to GitHub repositories

        Finds when people discuss projects that have matching repos
        """
        try:
            content = f"{node.title} {node.content or ''}".lower()

            # Extract potential project names (capitalized words, quoted phrases)
            project_patterns = [
                r'"([^"]+)"',  # Quoted phrases
                r'\b(Project\s+\w+)\b',  # "Project Phoenix"
                r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b'  # Capitalized phrases
            ]

            potential_projects = set()
            for pattern in project_patterns:
                matches = re.findall(pattern, node.title + ' ' + (node.content or ''))
                potential_projects.update(matches)

            # Find GitHub repos with matching names
            created = 0
            for project_name in potential_projects:
                if len(project_name) < 3:  # Too short
                    continue

                # Search for repos with similar names
                repos = await self.db.execute(
                    select(CrossSourceNode).where(
                        and_(
                            CrossSourceNode.team_id == team_id,
                            CrossSourceNode.source == 'github',
                            CrossSourceNode.node_type == NodeType.REPOSITORY,
                            or_(
                                CrossSourceNode.title.ilike(f'%{project_name}%'),
                                CrossSourceNode.source_id.ilike(f'%{project_name}%')
                            )
                        )
                    ).limit(3)
                )
                repos = repos.scalars().all()

                for repo in repos:
                    # Check if edge exists
                    if await self._edge_exists(node.canonical_id, repo.canonical_id, team_id):
                        continue

                    # Create project link
                    await self.link_service.create_edge(
                        source_node_id=node.canonical_id,
                        target_node_id=repo.canonical_id,
                        edge_type=EdgeType.DISCUSSES,
                        team_id=team_id,
                        confidence=0.6,
                        detection_method=DetectionMethod.KEYWORD_MATCH,
                        evidence=f"Project mention: {project_name}",
                        edge_metadata={
                            "project_mention": project_name,
                            "detection": "project_name_match"
                        }
                    )
                    created += 1

            return created

        except Exception as e:
            logger.error("project_link_detection_error", error=str(e))
            return 0

    async def _detect_temporal_links(
        self,
        node: CrossSourceNode,
        team_id: str,
        time_window_hours: int = 24
    ) -> int:
        """
        Link nodes created around the same time that mention similar entities

        Example: A Slack discussion followed by a GitHub PR on the same topic
        """
        try:
            # Only create temporal links for recent nodes (to avoid explosion of links)
            if not node.created_at:
                return 0

            age_days = (datetime.utcnow() - node.created_at).days
            if age_days > 7:  # Only for recent content
                return 0

            # Find nodes from OTHER sources within time window
            time_start = node.created_at - timedelta(hours=time_window_hours)
            time_end = node.created_at + timedelta(hours=time_window_hours)

            nearby_nodes = await self.db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.source != node.source,
                        CrossSourceNode.created_at >= time_start,
                        CrossSourceNode.created_at <= time_end,
                        CrossSourceNode.canonical_id != node.canonical_id
                    )
                ).limit(20)
            )
            nearby_nodes = nearby_nodes.scalars().all()

            created = 0
            for nearby_node in nearby_nodes:
                # Check for shared entities or keywords
                overlap_score = self._calculate_content_overlap(node, nearby_node)

                if overlap_score < 0.3:  # Not enough overlap
                    continue

                # Check if edge exists
                if await self._edge_exists(node.canonical_id, nearby_node.canonical_id, team_id):
                    continue

                # Create temporal link
                await self.link_service.create_edge(
                    source_node_id=node.canonical_id,
                    target_node_id=nearby_node.canonical_id,
                    edge_type=EdgeType.RELATED_TO,
                    team_id=team_id,
                    confidence=overlap_score,
                    detection_method=DetectionMethod.TEMPORAL_PROXIMITY,
                    evidence=f"Created within {time_window_hours}h, shared keywords",
                    edge_metadata={
                        "time_diff_hours": abs((node.created_at - nearby_node.created_at).total_seconds() / 3600),
                        "overlap_score": overlap_score
                    }
                )
                created += 1

            return created

        except Exception as e:
            logger.error("temporal_link_detection_error", error=str(e))
            return 0

    # ===================
    # HELPER METHODS
    # ===================

    async def _get_node(
        self,
        canonical_id: str,
        team_id: str
    ) -> Optional[CrossSourceNode]:
        """Get node by canonical ID"""
        result = await self.db.execute(
            select(CrossSourceNode).where(
                and_(
                    CrossSourceNode.canonical_id == canonical_id,
                    CrossSourceNode.team_id == team_id
                )
            )
        )
        return result.scalar_one_or_none()

    async def _edge_exists(
        self,
        source_id: str,
        target_id: str,
        team_id: str
    ) -> bool:
        """Check if any edge exists between two nodes"""
        result = await self.db.execute(
            select(func.count(CrossSourceEdge.id)).where(
                and_(
                    CrossSourceEdge.source_node_id == source_id,
                    CrossSourceEdge.target_node_id == target_id,
                    CrossSourceEdge.team_id == team_id,
                    CrossSourceEdge.is_deleted == "false"
                )
            )
        )
        count = result.scalar()
        return count > 0

    def _calculate_content_overlap(
        self,
        node1: CrossSourceNode,
        node2: CrossSourceNode
    ) -> float:
        """
        Calculate keyword overlap between two nodes

        Returns:
            Overlap score 0.0-1.0
        """
        try:
            # Extract keywords (simple version)
            words1 = set(re.findall(r'\b\w{4,}\b', (node1.content or '').lower()))
            words2 = set(re.findall(r'\b\w{4,}\b', (node2.content or '').lower()))

            if not words1 or not words2:
                return 0.0

            # Jaccard similarity
            intersection = len(words1 & words2)
            union = len(words1 | words2)

            return intersection / union if union > 0 else 0.0

        except Exception:
            return 0.0


def get_link_detector(db: AsyncSession) -> CrossSourceLinkDetector:
    """Get link detector instance"""
    return CrossSourceLinkDetector(db)
