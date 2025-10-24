"""
Cross-Source Knowledge Graph Traversal and Reasoning Engine

This service provides intelligent multi-hop traversal and reasoning capabilities
across the unified knowledge graph, enabling the AI to:

1. Find relevant context by traversing relationships across sources
2. Discover connections between seemingly unrelated items
3. Rank results by graph-based relevance
4. Provide reasoning explanations for answers
"""
from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func

from app.core.logging import get_logger
from app.models.cross_source_node import CrossSourceNode, NodeType
from app.models.cross_source_edge import CrossSourceEdge, EdgeType, DetectionMethod
from app.services.cross_source_link_service import CrossSourceLinkService

logger = get_logger(__name__)


class CrossSourceGraphService:
    """
    Advanced knowledge graph traversal and reasoning across all sources

    This service enables the AI to think across multiple knowledge sources
    (Slack, GitHub, Jira, Confluence, etc.) and provide contextually rich,
    well-reasoned answers.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.link_service = CrossSourceLinkService(db)

    async def expand_context_from_seed_nodes(
        self,
        seed_node_ids: List[str],
        team_id: str,
        max_depth: int = 2,
        max_nodes: int = 50,
        edge_type_weights: Optional[Dict[EdgeType, float]] = None
    ) -> Dict[str, Any]:
        """
        Expand context by traversing the graph from seed nodes

        Use case: Given initial search results, find related context that might
        be relevant even if it doesn't match the search query directly.

        Example: User searches "authentication bug" → finds GitHub PR #123 →
        this expands to find Slack messages discussing the PR, related issues,
        commits that fix it, etc.

        Args:
            seed_node_ids: Starting nodes (e.g., from vector search)
            team_id: Workspace team ID
            max_depth: How many hops to traverse
            max_nodes: Maximum nodes to return
            edge_type_weights: Optional weights for different relationship types

        Returns:
            Dict with:
                - nodes: List of expanded nodes with relevance scores
                - edges: List of edges connecting them
                - reasoning_paths: Explanation of how nodes are related
                - stats: Traversal statistics
        """
        try:
            logger.info(
                "context_expansion_started",
                team_id=team_id,
                seed_count=len(seed_node_ids),
                max_depth=max_depth
            )

            # Default edge weights (higher = more important)
            if not edge_type_weights:
                edge_type_weights = {
                    EdgeType.FIXES: 1.0,
                    EdgeType.RESOLVED_BY: 1.0,
                    EdgeType.IMPLEMENTS: 0.9,
                    EdgeType.MODIFIES: 0.8,
                    EdgeType.REFERENCES: 0.6,
                    EdgeType.DISCUSSES: 0.7,
                    EdgeType.PART_OF: 0.8,
                    EdgeType.CHILD_OF: 0.8,
                }

            expanded_nodes = {}  # canonical_id -> (node, depth, score)
            edges_found = []
            reasoning_paths = []

            # Load seed nodes
            for seed_id in seed_node_ids:
                result = await self.db.execute(
                    select(CrossSourceNode).where(
                        and_(
                            CrossSourceNode.canonical_id == seed_id,
                            CrossSourceNode.team_id == team_id
                        )
                    )
                )
                node = result.scalar_one_or_none()
                if node:
                    expanded_nodes[seed_id] = (node, 0, 1.0)  # Seed nodes have score 1.0

            # BFS traversal
            current_depth = 0
            frontier = set(seed_node_ids)

            while current_depth < max_depth and frontier and len(expanded_nodes) < max_nodes:
                current_depth += 1
                next_frontier = set()

                for node_id in frontier:
                    # Get outbound edges
                    outbound = await self.link_service.get_outbound_edges(
                        node_id,
                        team_id,
                        min_confidence=0.5
                    )

                    # Get inbound edges (things that reference this)
                    inbound = await self.link_service.get_inbound_edges(
                        node_id,
                        team_id,
                        min_confidence=0.5
                    )

                    # Process edges
                    for edge in outbound + inbound:
                        edges_found.append(edge)

                        # Determine target node
                        target_id = edge.target_node_id if edge.source_node_id == node_id else edge.source_node_id

                        if target_id in expanded_nodes:
                            continue

                        # Calculate relevance score
                        edge_weight = edge_type_weights.get(edge.edge_type, 0.5)
                        depth_penalty = 0.7 ** current_depth  # Decay with distance
                        score = edge.confidence * edge_weight * depth_penalty

                        # Load target node
                        result = await self.db.execute(
                            select(CrossSourceNode).where(
                                CrossSourceNode.canonical_id == target_id
                            )
                        )
                        target_node = result.scalar_one_or_none()

                        if target_node:
                            expanded_nodes[target_id] = (target_node, current_depth, score)
                            next_frontier.add(target_id)

                            # Track reasoning
                            reasoning_paths.append({
                                'from': node_id,
                                'to': target_id,
                                'via': edge.edge_type.value,
                                'evidence': edge.evidence,
                                'depth': current_depth,
                                'score': score
                            })

                frontier = next_frontier

            # Sort nodes by relevance score
            sorted_nodes = sorted(
                expanded_nodes.values(),
                key=lambda x: x[2],
                reverse=True
            )[:max_nodes]

            result = {
                'nodes': [
                    {
                        **node.to_dict(),
                        'depth': depth,
                        'relevance_score': score
                    }
                    for node, depth, score in sorted_nodes
                ],
                'edges': [edge.to_dict() for edge in edges_found],
                'reasoning_paths': reasoning_paths,
                'stats': {
                    'seed_count': len(seed_node_ids),
                    'total_nodes': len(expanded_nodes),
                    'total_edges': len(edges_found),
                    'max_depth_reached': current_depth
                }
            }

            logger.info(
                "context_expansion_completed",
                team_id=team_id,
                nodes_found=len(expanded_nodes),
                edges_found=len(edges_found)
            )

            return result

        except Exception as e:
            logger.error(
                "context_expansion_failed",
                team_id=team_id,
                error=str(e)
            )
            raise

    async def find_related_discussions(
        self,
        canonical_id: str,
        team_id: str,
        source_types: Optional[List[str]] = None,
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Find discussions related to a specific node

        Use case: "Show me Slack conversations about this GitHub PR"

        Args:
            canonical_id: Node to find discussions about
            team_id: Team ID
            source_types: Optional filter (e.g., ["slack"] for only Slack messages)
            max_results: Maximum results to return

        Returns:
            List of related nodes with relationship info
        """
        try:
            # Get all edges connected to this node
            outbound = await self.link_service.get_outbound_edges(canonical_id, team_id)
            inbound = await self.link_service.get_inbound_edges(canonical_id, team_id)

            all_edges = outbound + inbound
            related_node_ids = set()

            for edge in all_edges:
                if edge.edge_type in [EdgeType.DISCUSSES, EdgeType.REFERENCES]:
                    target_id = edge.target_node_id if edge.source_node_id == canonical_id else edge.source_node_id
                    related_node_ids.add(target_id)

            # Load related nodes
            if not related_node_ids:
                return []

            conditions = [
                CrossSourceNode.canonical_id.in_(list(related_node_ids)),
                CrossSourceNode.team_id == team_id,
                CrossSourceNode.is_deleted == "false"
            ]

            if source_types:
                conditions.append(CrossSourceNode.source.in_(source_types))

            result = await self.db.execute(
                select(CrossSourceNode).where(and_(*conditions)).limit(max_results)
            )

            nodes = result.scalars().all()

            return [
                {
                    **node.to_dict(),
                    'relationship': 'discusses' if any(
                        e.edge_type == EdgeType.DISCUSSES
                        for e in all_edges
                        if node.canonical_id in [e.source_node_id, e.target_node_id]
                    ) else 'references'
                }
                for node in nodes
            ]

        except Exception as e:
            logger.error(
                "find_related_discussions_failed",
                canonical_id=canonical_id,
                error=str(e)
            )
            return []

    async def explain_relationship(
        self,
        node_a_id: str,
        node_b_id: str,
        team_id: str,
        max_path_length: int = 3
    ) -> Optional[Dict[str, Any]]:
        """
        Explain how two nodes are related

        Use case: "How is this Slack message connected to that Jira ticket?"

        Args:
            node_a_id: First node
            node_b_id: Second node
            team_id: Team ID
            max_path_length: Maximum hops to search

        Returns:
            Dict with explanation of relationship, or None if not connected
        """
        try:
            # Find paths between nodes
            paths = await self.link_service.find_paths(
                node_a_id,
                node_b_id,
                team_id,
                max_depth=max_path_length,
                min_confidence=0.5
            )

            if not paths:
                return None

            # Find best path (shortest with highest confidence)
            best_path = min(paths, key=lambda p: (len(p), -sum(e.confidence for e in p)))

            # Build explanation
            path_description = []
            for edge in best_path:
                path_description.append({
                    'from': edge.source_node_id,
                    'to': edge.target_node_id,
                    'relationship': edge.edge_type.value,
                    'evidence': edge.evidence,
                    'confidence': edge.confidence
                })

            # Load nodes in path
            node_ids = {edge.source_node_id for edge in best_path} | {edge.target_node_id for edge in best_path}
            result = await self.db.execute(
                select(CrossSourceNode).where(
                    CrossSourceNode.canonical_id.in_(list(node_ids))
                )
            )
            nodes = {node.canonical_id: node for node in result.scalars().all()}

            return {
                'connected': True,
                'path_length': len(best_path),
                'confidence': sum(e.confidence for e in best_path) / len(best_path),
                'path': path_description,
                'nodes': {nid: nodes[nid].to_dict() for nid in node_ids if nid in nodes},
                'explanation': self._generate_path_explanation(best_path, nodes)
            }

        except Exception as e:
            logger.error(
                "explain_relationship_failed",
                node_a=node_a_id,
                node_b=node_b_id,
                error=str(e)
            )
            return None

    def _generate_path_explanation(
        self,
        path: List[CrossSourceEdge],
        nodes: Dict[str, CrossSourceNode]
    ) -> str:
        """Generate human-readable explanation of a path"""
        if not path:
            return "No connection found"

        parts = []
        for edge in path:
            source = nodes.get(edge.source_node_id)
            target = nodes.get(edge.target_node_id)

            if source and target:
                source_desc = f"{source.node_type.value} '{source.title[:50]}'"
                target_desc = f"{target.node_type.value} '{target.title[:50]}'"
                parts.append(f"{source_desc} {edge.edge_type.value} {target_desc}")

        return " → ".join(parts)

    async def get_node_importance_score(
        self,
        canonical_id: str,
        team_id: str
    ) -> float:
        """
        Calculate importance score for a node based on graph structure

        Considers:
        - Number of inbound/outbound edges (centrality)
        - Edge types and quality
        - Recency
        - Node type

        Returns:
            Importance score (0.0-1.0)
        """
        try:
            # Get node
            result = await self.db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.canonical_id == canonical_id,
                        CrossSourceNode.team_id == team_id
                    )
                )
            )
            node = result.scalar_one_or_none()

            if not node:
                return 0.0

            # Count edges
            inbound = await self.link_service.get_inbound_edges(canonical_id, team_id)
            outbound = await self.link_service.get_outbound_edges(canonical_id, team_id)

            # Calculate components
            edge_score = min(1.0, (len(inbound) + len(outbound)) / 20.0)  # Normalize to 0-1

            # High-value edge types boost score
            high_value_edges = sum(
                1 for e in inbound + outbound
                if e.edge_type in [EdgeType.FIXES, EdgeType.IMPLEMENTS, EdgeType.RESOLVED_BY]
            )
            quality_score = min(1.0, high_value_edges / 5.0)

            # Recency score (newer = more important)
            age_days = (datetime.utcnow() - node.created_at).days
            recency_score = max(0.0, 1.0 - (age_days / 90.0))  # Decay over 90 days

            # Node type weights
            type_weights = {
                NodeType.PULL_REQUEST: 1.0,
                NodeType.ISSUE: 0.9,
                NodeType.CODE_FILE: 0.8,
                NodeType.MESSAGE: 0.6,
            }
            type_score = type_weights.get(node.node_type, 0.5)

            # Weighted combination
            importance = (
                edge_score * 0.3 +
                quality_score * 0.3 +
                recency_score * 0.2 +
                type_score * 0.2
            )

            return importance

        except Exception as e:
            logger.error(
                "importance_calculation_failed",
                canonical_id=canonical_id,
                error=str(e)
            )
            return 0.0

    async def find_experts(
        self,
        topic_node_ids: List[str],
        team_id: str,
        max_experts: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find people who are experts on given topics

        Use case: "Who knows about authentication?" → finds people who:
        - Modified related code files
        - Created/resolved related issues
        - Discussed the topic frequently

        Args:
            topic_node_ids: Nodes representing topics (code files, issues, etc.)
            team_id: Team ID
            max_experts: Maximum experts to return

        Returns:
            List of experts with contribution counts and expertise scores
        """
        try:
            expert_contributions = {}  # author -> {contributions, node_types, recency}

            for node_id in topic_node_ids:
                # Get the topic node
                result = await self.db.execute(
                    select(CrossSourceNode).where(
                        CrossSourceNode.canonical_id == node_id
                    )
                )
                topic_node = result.scalar_one_or_none()

                if not topic_node:
                    continue

                # Find nodes that modify/implement/discuss this topic
                inbound = await self.link_service.get_inbound_edges(
                    node_id,
                    team_id,
                    edge_types=[EdgeType.MODIFIES, EdgeType.IMPLEMENTS, EdgeType.DISCUSSES]
                )

                # Load contributor nodes
                contributor_ids = [e.source_node_id for e in inbound]
                if contributor_ids:
                    result = await self.db.execute(
                        select(CrossSourceNode).where(
                            CrossSourceNode.canonical_id.in_(contributor_ids)
                        )
                    )
                    contributor_nodes = result.scalars().all()

                    for contrib_node in contributor_nodes:
                        if not contrib_node.author:
                            continue

                        if contrib_node.author not in expert_contributions:
                            expert_contributions[contrib_node.author] = {
                                'contributions': 0,
                                'node_types': set(),
                                'most_recent': contrib_node.created_at
                            }

                        expert_contributions[contrib_node.author]['contributions'] += 1
                        expert_contributions[contrib_node.author]['node_types'].add(
                            contrib_node.node_type.value
                        )
                        expert_contributions[contrib_node.author]['most_recent'] = max(
                            expert_contributions[contrib_node.author]['most_recent'],
                            contrib_node.created_at
                        )

            # Rank experts
            ranked_experts = []
            for author, stats in expert_contributions.items():
                # Score based on contributions, diversity, and recency
                contribution_score = min(1.0, stats['contributions'] / 10.0)
                diversity_score = min(1.0, len(stats['node_types']) / 3.0)

                age_days = (datetime.utcnow() - stats['most_recent']).days
                recency_score = max(0.0, 1.0 - (age_days / 180.0))

                overall_score = (
                    contribution_score * 0.5 +
                    diversity_score * 0.3 +
                    recency_score * 0.2
                )

                ranked_experts.append({
                    'author': author,
                    'contributions': stats['contributions'],
                    'contribution_types': list(stats['node_types']),
                    'most_recent_activity': stats['most_recent'].isoformat(),
                    'expertise_score': overall_score
                })

            # Sort by score
            ranked_experts.sort(key=lambda x: x['expertise_score'], reverse=True)

            return ranked_experts[:max_experts]

        except Exception as e:
            logger.error(
                "find_experts_failed",
                error=str(e)
            )
            return []


    async def find_entities_in_content(
        self,
        canonical_id: str,
        team_id: str,
        entity_types: Optional[List[NodeType]] = None
    ) -> List[Dict[str, Any]]:
        """
        Find entities mentioned/referenced in a piece of content

        Use case: "What topics/people are discussed in this Slack thread?"

        Args:
            canonical_id: Content node ID (message, PR, issue, etc.)
            team_id: Team ID
            entity_types: Optional filter for specific entity types (PERSON, TOPIC, etc.)

        Returns:
            List of entity nodes related to this content
        """
        try:
            # Get edges where content mentions/involves/is-about entities
            relevant_edge_types = [
                EdgeType.ABOUT,
                EdgeType.INVOLVES,
                EdgeType.TAGGED_WITH,
                EdgeType.AUTHORED_BY
            ]

            # Get outbound edges from content to entities
            outbound = await self.link_service.get_outbound_edges(
                canonical_id,
                team_id,
                edge_types=relevant_edge_types
            )

            if not outbound:
                return []

            # Get target entity nodes
            entity_ids = [edge.target_node_id for edge in outbound]

            conditions = [
                CrossSourceNode.canonical_id.in_(entity_ids),
                CrossSourceNode.team_id == team_id,
                CrossSourceNode.source == "derived"  # Entities have source='derived'
            ]

            if entity_types:
                conditions.append(CrossSourceNode.node_type.in_(entity_types))

            result = await self.db.execute(
                select(CrossSourceNode).where(and_(*conditions))
            )
            entities = result.scalars().all()

            # Enrich with relationship info
            return [
                {
                    **entity.to_dict(),
                    'relationship_type': next(
                        (e.edge_type.value for e in outbound if e.target_node_id == entity.canonical_id),
                        'unknown'
                    ),
                    'confidence': next(
                        (e.confidence for e in outbound if e.target_node_id == entity.canonical_id),
                        0.0
                    )
                }
                for entity in entities
            ]

        except Exception as e:
            logger.error(
                "find_entities_in_content_failed",
                canonical_id=canonical_id,
                error=str(e)
            )
            return []

    async def find_content_about_entity(
        self,
        entity_canonical_id: str,
        team_id: str,
        source_types: Optional[List[str]] = None,
        node_types: Optional[List[NodeType]] = None,
        max_results: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Find all content (messages, PRs, issues, etc.) that discusses/mentions an entity

        Use case: "Show me all Slack messages and GitHub PRs about 'authentication'"

        Args:
            entity_canonical_id: Entity node ID (e.g., "entity:topic:authentication")
            team_id: Team ID
            source_types: Optional filter by source (e.g., ["slack", "github"])
            node_types: Optional filter by node type (e.g., [NodeType.MESSAGE, NodeType.PULL_REQUEST])
            max_results: Maximum results

        Returns:
            List of content nodes that reference this entity
        """
        try:
            # Get inbound edges to entity (content -> entity)
            relevant_edge_types = [
                EdgeType.ABOUT,
                EdgeType.INVOLVES,
                EdgeType.TAGGED_WITH,
                EdgeType.MENTIONS
            ]

            inbound = await self.link_service.get_inbound_edges(
                entity_canonical_id,
                team_id,
                edge_types=relevant_edge_types,
                min_confidence=0.3
            )

            if not inbound:
                return []

            # Get source content nodes
            content_ids = [edge.source_node_id for edge in inbound]

            conditions = [
                CrossSourceNode.canonical_id.in_(content_ids),
                CrossSourceNode.team_id == team_id,
                CrossSourceNode.is_deleted == "false"
            ]

            if source_types:
                conditions.append(CrossSourceNode.source.in_(source_types))

            if node_types:
                conditions.append(CrossSourceNode.node_type.in_(node_types))

            result = await self.db.execute(
                select(CrossSourceNode)
                .where(and_(*conditions))
                .order_by(CrossSourceNode.created_at.desc())
                .limit(max_results)
            )
            content_nodes = result.scalars().all()

            # Enrich with relationship info
            return [
                {
                    **node.to_dict(),
                    'relationship_type': next(
                        (e.edge_type.value for e in inbound if e.source_node_id == node.canonical_id),
                        'unknown'
                    ),
                    'confidence': next(
                        (e.confidence for e in inbound if e.source_node_id == node.canonical_id),
                        0.0
                    )
                }
                for node in content_nodes
            ]

        except Exception as e:
            logger.error(
                "find_content_about_entity_failed",
                entity_id=entity_canonical_id,
                error=str(e)
            )
            return []

    async def get_entity_expertise(
        self,
        person_canonical_id: str,
        team_id: str,
        max_topics: int = 10
    ) -> Dict[str, Any]:
        """
        Get topics/technologies a person is expert in

        Use case: "What does John know about?" or "Who should I ask about Redis?"

        Args:
            person_canonical_id: Person entity node ID (e.g., "entity:person:john_doe")
            team_id: Team ID
            max_topics: Maximum topics to return

        Returns:
            Dict with person info and their areas of expertise
        """
        try:
            # Get person node
            result = await self.db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.canonical_id == person_canonical_id,
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.node_type == NodeType.PERSON
                    )
                )
            )
            person_node = result.scalar_one_or_none()

            if not person_node:
                return {"found": False}

            # Find topics/technologies via EXPERTISE_IN edges
            expertise_edges = await self.link_service.get_outbound_edges(
                person_canonical_id,
                team_id,
                edge_types=[EdgeType.EXPERTISE_IN, EdgeType.WORKS_WITH]
            )

            if not expertise_edges:
                return {
                    "found": True,
                    "person": person_node.to_dict(),
                    "expertise_areas": []
                }

            # Load expertise nodes
            topic_ids = [e.target_node_id for e in expertise_edges]
            result = await self.db.execute(
                select(CrossSourceNode).where(
                    CrossSourceNode.canonical_id.in_(topic_ids)
                )
            )
            topic_nodes = {node.canonical_id: node for node in result.scalars().all()}

            # Build expertise list
            expertise_areas = []
            for edge in expertise_edges:
                topic = topic_nodes.get(edge.target_node_id)
                if topic:
                    expertise_areas.append({
                        "topic": topic.title,
                        "canonical_id": topic.canonical_id,
                        "type": topic.node_type.value,
                        "confidence": edge.confidence,
                        "evidence": edge.evidence
                    })

            # Sort by confidence
            expertise_areas.sort(key=lambda x: x["confidence"], reverse=True)

            return {
                "found": True,
                "person": person_node.to_dict(),
                "expertise_areas": expertise_areas[:max_topics]
            }

        except Exception as e:
            logger.error(
                "get_entity_expertise_failed",
                person_id=person_canonical_id,
                error=str(e)
            )
            return {"found": False, "error": str(e)}

    async def find_related_entities(
        self,
        entity_canonical_id: str,
        team_id: str,
        min_confidence: float = 0.3,
        max_results: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Find entities related to a given entity

        Use case: "What topics are related to 'authentication'?"

        Args:
            entity_canonical_id: Entity node ID
            team_id: Team ID
            min_confidence: Minimum confidence threshold
            max_results: Maximum results

        Returns:
            List of related entity nodes with relationship info
        """
        try:
            # Get entity-to-entity edges
            entity_edge_types = [
                EdgeType.RELATED_TO,
                EdgeType.SIMILAR_TO,
                EdgeType.CO_OCCURS_WITH,
                EdgeType.PARENT_TOPIC,
                EdgeType.WORKS_WITH
            ]

            # Get both outbound and inbound edges
            outbound = await self.link_service.get_outbound_edges(
                entity_canonical_id,
                team_id,
                edge_types=entity_edge_types,
                min_confidence=min_confidence
            )

            inbound = await self.link_service.get_inbound_edges(
                entity_canonical_id,
                team_id,
                edge_types=entity_edge_types,
                min_confidence=min_confidence
            )

            all_edges = outbound + inbound

            if not all_edges:
                return []

            # Get related entity IDs
            related_ids = set()
            for edge in all_edges:
                if edge.source_node_id == entity_canonical_id:
                    related_ids.add(edge.target_node_id)
                else:
                    related_ids.add(edge.source_node_id)

            # Load related entities
            result = await self.db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.canonical_id.in_(list(related_ids)),
                        CrossSourceNode.source == "derived"
                    )
                )
            )
            entities = result.scalars().all()

            # Enrich with relationship info
            related = []
            for entity in entities:
                # Find edge connecting to this entity
                connecting_edge = next(
                    (e for e in all_edges
                     if entity.canonical_id in [e.source_node_id, e.target_node_id]),
                    None
                )

                if connecting_edge:
                    related.append({
                        **entity.to_dict(),
                        'relationship_type': connecting_edge.edge_type.value,
                        'confidence': connecting_edge.confidence,
                        'co_occurrence_count': connecting_edge.edge_metadata.get('co_occurrence_count', 0) if connecting_edge.edge_metadata else 0
                    })

            # Sort by confidence
            related.sort(key=lambda x: x['confidence'], reverse=True)

            return related[:max_results]

        except Exception as e:
            logger.error(
                "find_related_entities_failed",
                entity_id=entity_canonical_id,
                error=str(e)
            )
            return []

    async def get_entity_timeline(
        self,
        entity_canonical_id: str,
        team_id: str,
        days_back: int = 90
    ) -> Dict[str, Any]:
        """
        Track entity mentions over time across all sources

        Use case: "When was 'migration' discussed most actively?"

        Args:
            entity_canonical_id: Entity node ID
            team_id: Team ID
            days_back: How many days to look back

        Returns:
            Timeline data with mention counts by source and time period
        """
        try:
            # Get entity node
            result = await self.db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.canonical_id == entity_canonical_id,
                        CrossSourceNode.team_id == team_id
                    )
                )
            )
            entity_node = result.scalar_one_or_none()

            if not entity_node:
                return {"found": False}

            # Get all content mentioning this entity
            content_nodes = await self.find_content_about_entity(
                entity_canonical_id,
                team_id,
                max_results=500
            )

            if not content_nodes:
                return {
                    "found": True,
                    "entity": entity_node.to_dict(),
                    "mentions": 0
                }

            # Filter by time range
            since_date = datetime.utcnow() - timedelta(days=days_back)
            recent_content = [
                node for node in content_nodes
                if datetime.fromisoformat(node['created_at'].replace('Z', '+00:00')) >= since_date
            ]

            # Group by week and source
            timeline = {}  # week -> {source -> count}
            source_totals = {}  # source -> total count

            for node in recent_content:
                created_at = datetime.fromisoformat(node['created_at'].replace('Z', '+00:00'))
                week_key = created_at.strftime("%Y-W%W")
                source = node['source']

                if week_key not in timeline:
                    timeline[week_key] = {}

                if source not in timeline[week_key]:
                    timeline[week_key][source] = 0

                timeline[week_key][source] += 1

                # Track source totals
                source_totals[source] = source_totals.get(source, 0) + 1

            # Find peak activity week
            peak_week = max(
                timeline.items(),
                key=lambda x: sum(x[1].values())
            ) if timeline else (None, {})

            return {
                "found": True,
                "entity": entity_node.to_dict(),
                "total_mentions": len(recent_content),
                "date_range": {
                    "start": since_date.isoformat(),
                    "end": datetime.utcnow().isoformat()
                },
                "mentions_by_source": source_totals,
                "timeline": timeline,
                "peak_activity": {
                    "week": peak_week[0],
                    "mentions": sum(peak_week[1].values()) if peak_week[0] else 0,
                    "sources": peak_week[1]
                } if peak_week[0] else None
            }

        except Exception as e:
            logger.error(
                "get_entity_timeline_failed",
                entity_id=entity_canonical_id,
                error=str(e)
            )
            return {"found": False, "error": str(e)}


def get_cross_source_graph_service(db: AsyncSession) -> CrossSourceGraphService:
    """Get cross-source graph service instance"""
    return CrossSourceGraphService(db)
