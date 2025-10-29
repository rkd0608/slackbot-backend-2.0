"""
Cross-source link detection and graph building service

This service:
1. Detects relationships between nodes from different sources
2. Creates edges in the knowledge graph
3. Maintains link confidence scores
4. Enables multi-hop traversal across sources
"""
import uuid
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.core.logging import get_logger
from app.models.cross_source_node import CrossSourceNode
from app.models.cross_source_edge import CrossSourceEdge, EdgeType, DetectionMethod
from app.integrations.registry import IntegrationRegistry

logger = get_logger(__name__)


class CrossSourceLinkService:
    """
    Service for detecting and managing relationships between knowledge graph nodes

    Key Features:
    - Automatic link detection when nodes are added
    - Support for multiple detection methods (explicit, semantic, temporal)
    - Bidirectional relationship management
    - Confidence scoring
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect_and_create_links(
        self,
        node: CrossSourceNode,
        team_id: str
    ) -> Dict[str, Any]:
        """
        Detect all outbound links from a node and create edges

        This is called when a new node is added to the knowledge graph.

        Args:
            node: Source node to detect links from
            team_id: Workspace team ID

        Returns:
            Dict with link statistics
        """
        try:
            logger.info(
                "link_detection_started",
                canonical_id=node.canonical_id,
                team_id=team_id
            )

            stats = {
                'total_detected': 0,
                'created': 0,
                'already_exists': 0,
                'failed': 0,
                'by_method': {}
            }

            # Get adapter for this source
            # Note: We need credentials, but for link detection we only need the adapter's
            # detect_outbound_links method which doesn't require API calls
            # In a real implementation, you'd load credentials from the database
            # For now, we'll use a simplified approach

            detected_links = await self._detect_links_from_node(node, team_id)
            stats['total_detected'] = len(detected_links)

            # Create edges for each detected link
            for link in detected_links:
                try:
                    # Check if target node exists
                    target_canonical_id = f"{link['target_source']}:{link['target_id']}"

                    result = await self.db.execute(
                        select(CrossSourceNode).where(
                            and_(
                                CrossSourceNode.canonical_id == target_canonical_id,
                                CrossSourceNode.team_id == team_id
                            )
                        )
                    )
                    target_node = result.scalar_one_or_none()

                    if not target_node:
                        # Target doesn't exist yet - skip for now
                        # In production, you might want to create a placeholder or queue for later
                        logger.debug(
                            "link_target_not_found",
                            source=node.canonical_id,
                            target=target_canonical_id
                        )
                        stats['failed'] += 1
                        continue

                    # Determine edge type from link metadata
                    edge_type = self._infer_edge_type(link, node, target_node)

                    # Check if edge already exists
                    existing = await self._get_edge(
                        node.canonical_id,
                        target_canonical_id,
                        edge_type,
                        team_id
                    )

                    if existing:
                        stats['already_exists'] += 1
                        continue

                    # Create new edge
                    edge = await self.create_edge(
                        source_node_id=node.canonical_id,
                        target_node_id=target_canonical_id,
                        edge_type=edge_type,
                        team_id=team_id,
                        confidence=link.get('confidence', 1.0),
                        detection_method=DetectionMethod.EXPLICIT_LINK,  # Most links are explicit
                        evidence=link.get('evidence', ''),
                        link_text=link.get('link_text', '')
                    )

                    stats['created'] += 1

                    # Track by method
                    method = DetectionMethod.EXPLICIT_LINK.value
                    stats['by_method'][method] = stats['by_method'].get(method, 0) + 1

                except Exception as e:
                    logger.error(
                        "edge_creation_failed",
                        source=node.canonical_id,
                        link=link,
                        error=str(e)
                    )
                    stats['failed'] += 1

            await self.db.commit()

            logger.info(
                "link_detection_completed",
                canonical_id=node.canonical_id,
                stats=stats
            )

            return stats

        except Exception as e:
            logger.error(
                "link_detection_failed",
                canonical_id=node.canonical_id,
                error=str(e)
            )
            await self.db.rollback()
            raise

    async def _detect_links_from_node(
        self,
        node: CrossSourceNode,
        team_id: str
    ) -> List[Dict[str, Any]]:
        """
        Use source adapter to detect outbound links

        Args:
            node: Node to detect links from
            team_id: Team ID

        Returns:
            List of detected link dicts
        """
        try:
            # Import here to avoid circular dependencies
            from app.integrations.base_adapter import UnifiedDocument

            # Convert CrossSourceNode to UnifiedDocument for adapter
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

            # Get adapter
            # Note: In production, load credentials from database
            # For now, return empty list if adapter not available
            try:
                # This is a simplified version - in production you'd load actual credentials
                adapter = IntegrationRegistry.get_adapter(
                    source_name=node.source,
                    team_id=team_id,
                    credentials={}  # Placeholder
                )

                if adapter:
                    links = await adapter.detect_outbound_links(doc)
                    return links
            except Exception as e:
                logger.warning(
                    "adapter_link_detection_failed",
                    source=node.source,
                    error=str(e)
                )

            return []

        except Exception as e:
            logger.error(
                "link_detection_from_node_failed",
                canonical_id=node.canonical_id,
                error=str(e)
            )
            return []

    def _infer_edge_type(
        self,
        link: Dict[str, Any],
        source_node: CrossSourceNode,
        target_node: CrossSourceNode
    ) -> EdgeType:
        """
        Infer edge type from link metadata and node context

        Args:
            link: Detected link dict
            source_node: Source node
            target_node: Target node

        Returns:
            EdgeType enum value
        """
        # Check evidence text for keywords
        evidence = link.get('evidence', '').lower()
        link_text = link.get('link_text', '').lower()

        # Fix/resolve patterns
        if any(word in link_text for word in ['fixes', 'fix', 'fixed']):
            return EdgeType.FIXES
        if any(word in link_text for word in ['resolves', 'resolved', 'closes', 'closed']):
            return EdgeType.RESOLVED_BY

        # Code modification patterns
        if source_node.node_type.value == 'pull_request' and target_node.node_type.value == 'code_file':
            return EdgeType.MODIFIES

        # Implementation patterns
        if 'implements' in link_text or 'implementation' in link_text:
            return EdgeType.IMPLEMENTS

        # Hierarchical patterns
        if 'part of' in link_text or 'child of' in link_text:
            return EdgeType.CHILD_OF

        # Default to generic reference
        return EdgeType.REFERENCES

    async def create_edge(
        self,
        source_node_id: str,
        target_node_id: str,
        edge_type: EdgeType,
        team_id: str,
        confidence: float = 1.0,
        detection_method: DetectionMethod = DetectionMethod.EXPLICIT_LINK,
        evidence: str = '',
        link_text: str = '',
        edge_metadata: Optional[Dict] = None
    ) -> CrossSourceEdge:
        """
        Create a new edge in the knowledge graph

        Args:
            source_node_id: Source node canonical ID
            target_node_id: Target node canonical ID
            edge_type: Relationship type
            team_id: Workspace team ID
            confidence: Confidence score (0.0-1.0)
            detection_method: How the link was detected
            evidence: Why this relationship exists
            link_text: Actual text/URL that created the link
            edge_metadata: Additional metadata

        Returns:
            Created CrossSourceEdge
        """
        edge = CrossSourceEdge(
            id=str(uuid.uuid4()),
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            edge_type=edge_type,
            team_id=team_id,
            confidence=confidence,
            detection_method=detection_method,
            evidence=evidence,
            link_text=link_text,
            edge_metadata=edge_metadata or {},
            created_at=datetime.utcnow()
        )

        self.db.add(edge)

        logger.info(
            "edge_created",
            edge_id=edge.id,
            source=source_node_id,
            target=target_node_id,
            edge_type=edge_type.value
        )

        return edge

    async def _get_edge(
        self,
        source_node_id: str,
        target_node_id: str,
        edge_type: EdgeType,
        team_id: str
    ) -> Optional[CrossSourceEdge]:
        """Check if edge already exists"""
        result = await self.db.execute(
            select(CrossSourceEdge).where(
                and_(
                    CrossSourceEdge.source_node_id == source_node_id,
                    CrossSourceEdge.target_node_id == target_node_id,
                    CrossSourceEdge.edge_type == edge_type,
                    CrossSourceEdge.team_id == team_id,
                    CrossSourceEdge.is_deleted == "false"
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_outbound_edges(
        self,
        node_id: str,
        team_id: str,
        edge_types: Optional[List[EdgeType]] = None,
        min_confidence: float = 0.0
    ) -> List[CrossSourceEdge]:
        """
        Get all outbound edges from a node

        Args:
            node_id: Source node canonical ID
            team_id: Workspace team ID
            edge_types: Optional filter by edge types
            min_confidence: Minimum confidence threshold

        Returns:
            List of edges
        """
        conditions = [
            CrossSourceEdge.source_node_id == node_id,
            CrossSourceEdge.team_id == team_id,
            CrossSourceEdge.is_deleted == "false",
            CrossSourceEdge.confidence >= min_confidence
        ]

        if edge_types:
            conditions.append(CrossSourceEdge.edge_type.in_(edge_types))

        result = await self.db.execute(
            select(CrossSourceEdge).where(and_(*conditions))
        )

        return list(result.scalars().all())

    async def get_inbound_edges(
        self,
        node_id: str,
        team_id: str,
        edge_types: Optional[List[EdgeType]] = None,
        min_confidence: float = 0.0
    ) -> List[CrossSourceEdge]:
        """
        Get all inbound edges to a node

        Args:
            node_id: Target node canonical ID
            team_id: Workspace team ID
            edge_types: Optional filter by edge types
            min_confidence: Minimum confidence threshold

        Returns:
            List of edges
        """
        conditions = [
            CrossSourceEdge.target_node_id == node_id,
            CrossSourceEdge.team_id == team_id,
            CrossSourceEdge.is_deleted == "false",
            CrossSourceEdge.confidence >= min_confidence
        ]

        if edge_types:
            conditions.append(CrossSourceEdge.edge_type.in_(edge_types))

        result = await self.db.execute(
            select(CrossSourceEdge).where(and_(*conditions))
        )

        return list(result.scalars().all())

    async def find_paths(
        self,
        start_node_id: str,
        end_node_id: str,
        team_id: str,
        max_depth: int = 3,
        min_confidence: float = 0.5
    ) -> List[List[CrossSourceEdge]]:
        """
        Find paths between two nodes (multi-hop traversal)

        This enables reasoning like:
        "Find how this Slack message relates to that GitHub PR"

        Args:
            start_node_id: Starting node
            end_node_id: Target node
            team_id: Team ID
            max_depth: Maximum path length
            min_confidence: Minimum edge confidence

        Returns:
            List of paths (each path is a list of edges)
        """
        # This is a simplified BFS implementation
        # In production, use a graph database or more sophisticated algorithm

        paths = []
        visited = set()
        queue = [([start_node_id], [])]  # (nodes_visited, edges_in_path)

        while queue and len(paths) < 10:  # Limit to 10 paths
            current_path_nodes, current_path_edges = queue.pop(0)
            current_node = current_path_nodes[-1]

            if len(current_path_nodes) > max_depth:
                continue

            if current_node == end_node_id:
                paths.append(current_path_edges)
                continue

            if current_node in visited:
                continue

            visited.add(current_node)

            # Get outbound edges
            edges = await self.get_outbound_edges(
                current_node,
                team_id,
                min_confidence=min_confidence
            )

            for edge in edges:
                if edge.target_node_id not in current_path_nodes:  # Avoid cycles
                    queue.append((
                        current_path_nodes + [edge.target_node_id],
                        current_path_edges + [edge]
                    ))

        logger.info(
            "path_search_completed",
            start=start_node_id,
            end=end_node_id,
            paths_found=len(paths)
        )

        return paths


def get_link_service(db: AsyncSession) -> CrossSourceLinkService:
    """Get link service instance"""
    return CrossSourceLinkService(db)
