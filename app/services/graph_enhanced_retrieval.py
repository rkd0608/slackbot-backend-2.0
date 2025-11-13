"""
Graph-Enhanced Retrieval Service

Combines vector search with knowledge graph traversal for superior results.

Key innovations:
1. Multi-hop reasoning - Find answers through relationship chains
2. Path-based relevance - Score results by connection strength
3. Entity expansion - Automatically include related entities
4. Cross-source bridging - Connect Slack discussions to GitHub code
5. Temporal graph analysis - Consider time-based relationships
"""
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func

from app.core.logging import get_logger
from app.models.cross_source_node import CrossSourceNode, NodeType
from app.models.cross_source_edge import CrossSourceEdge, EdgeType
from app.services.cross_source_graph_service import CrossSourceGraphService
from app.core.vector_db import vector_db_manager

logger = get_logger(__name__)


class GraphEnhancedRetrieval:
    """
    Retrieval system that leverages knowledge graph structure

    Unlike traditional vector search (finds similar documents),
    this finds documents connected through meaningful relationships.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.graph_service = CrossSourceGraphService(db)

    async def retrieve_with_graph_expansion(
        self,
        query: str,
        team_id: str,
        initial_results: List[Dict[str, Any]],
        expansion_depth: int = 2,
        max_expanded: int = 30,
        diversify: bool = True
    ) -> Dict[str, Any]:
        """
        Enhance retrieval results with graph expansion

        Takes initial vector search results and expands via graph traversal
        to find related context that might not match the query text directly.

        Args:
            query: Original query
            team_id: Workspace team ID
            initial_results: Vector search results
            expansion_depth: How many hops in graph
            max_expanded: Maximum nodes to expand
            diversify: Whether to diversify across sources

        Returns:
            Dict with:
            - primary_results: Original results with scores
            - expanded_results: Graph-expanded results
            - reasoning: Why expanded results are relevant
            - stats: Expansion statistics
        """
        try:
            logger.info(
                "graph_expansion_started",
                query=query,
                initial_count=len(initial_results),
                expansion_depth=expansion_depth
            )

            if not initial_results:
                return {
                    'primary_results': [],
                    'expanded_results': [],
                    'reasoning': [],
                    'stats': {'seed_count': 0, 'expanded_count': 0}
                }

            # Extract seed node IDs
            seed_ids = []
            for result in initial_results[:10]:  # Use top 10 as seeds
                canonical_id = result.get('canonical_id')
                if canonical_id:
                    seed_ids.append(canonical_id)

            if not seed_ids:
                return {
                    'primary_results': initial_results,
                    'expanded_results': [],
                    'reasoning': [],
                    'stats': {'seed_count': 0, 'expanded_count': 0}
                }

            # Expand context via graph
            expansion = await self.graph_service.expand_context_from_seed_nodes(
                seed_node_ids=seed_ids,
                team_id=team_id,
                max_depth=expansion_depth,
                max_nodes=max_expanded
            )

            # Convert expanded nodes to result format
            expanded_results = []
            for node_dict in expansion.get('nodes', []):
                # node_dict is already a dictionary from to_dict()
                canonical_id = node_dict['canonical_id']

                # Skip if already in primary results
                if canonical_id in seed_ids:
                    continue

                expanded_results.append({
                    'canonical_id': canonical_id,
                    'source': node_dict['source'],
                    'source_type': node_dict['source'],
                    'node_type': node_dict.get('node_type', 'unknown'),
                    'title': node_dict['title'],
                    'content': node_dict.get('content', ''),
                    'url': node_dict.get('url', ''),
                    'author': node_dict.get('author'),
                    'timestamp': node_dict.get('created_at'),
                    'score': node_dict.get('relevance_score', 0.5),
                    'expansion_depth': node_dict.get('depth', 0),
                    'retrieval_method': 'graph_expansion'
                })

            # Optionally diversify
            if diversify:
                expanded_results = self._diversify_results(expanded_results)

            logger.info(
                "graph_expansion_completed",
                query=query,
                initial_count=len(initial_results),
                expanded_count=len(expanded_results),
                total_nodes_explored=expansion.get('stats', {}).get('nodes_explored', 0)
            )

            return {
                'primary_results': initial_results,
                'expanded_results': expanded_results,
                'reasoning': expansion.get('reasoning_paths', []),
                'stats': {
                    'seed_count': len(seed_ids),
                    'expanded_count': len(expanded_results),
                    'nodes_explored': expansion.get('stats', {}).get('nodes_explored', 0),
                    'edges_traversed': expansion.get('stats', {}).get('edges_traversed', 0)
                }
            }

        except Exception as e:
            logger.error("graph_expansion_error", error=str(e), query=query)
            return {
                'primary_results': initial_results,
                'expanded_results': [],
                'reasoning': [],
                'stats': {'seed_count': 0, 'expanded_count': 0},
                'error': str(e)
            }

    async def find_paths_between_entities(
        self,
        entity1: str,
        entity2: str,
        team_id: str,
        max_depth: int = 3,
        max_paths: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find paths connecting two entities in the graph

        Use case: "How is authentication related to the deployment issue?"
        - Finds connection: Authentication PR → Commit → Deployment

        Args:
            entity1: First entity/node canonical ID
            entity2: Second entity/node canonical ID
            team_id: Workspace team ID
            max_depth: Maximum path length
            max_paths: Maximum number of paths to return

        Returns:
            List of paths with nodes and edges
        """
        try:
            paths = await self.graph_service.link_service.find_paths(
                start_node_id=entity1,
                end_node_id=entity2,
                team_id=team_id,
                max_depth=max_depth,
                min_confidence=0.5
            )

            # Enrich paths with node data
            enriched_paths = []
            for path_edges in paths[:max_paths]:
                # Load nodes
                nodes = []
                for edge in path_edges:
                    # Load source node
                    source = await self._get_node(edge.source_node_id, team_id)
                    if source and source not in nodes:
                        nodes.append(source)

                    # Load target node
                    target = await self._get_node(edge.target_node_id, team_id)
                    if target and target not in nodes:
                        nodes.append(target)

                # Calculate path strength
                path_strength = 1.0
                for edge in path_edges:
                    path_strength *= edge.confidence

                enriched_paths.append({
                    'nodes': [self._node_to_dict(n) for n in nodes],
                    'edges': [self._edge_to_dict(e) for e in path_edges],
                    'path_strength': path_strength,
                    'path_length': len(path_edges)
                })

            logger.info(
                "paths_found",
                entity1=entity1,
                entity2=entity2,
                paths_count=len(enriched_paths)
            )

            return enriched_paths

        except Exception as e:
            logger.error("find_paths_error", error=str(e))
            return []

    async def retrieve_by_entity_relationships(
        self,
        entities: List[str],
        team_id: str,
        relationship_types: Optional[List[EdgeType]] = None,
        max_results: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Retrieve nodes related to specific entities via relationships

        Use case: Find everything connected to "authentication" and "API"

        Args:
            entities: List of entity names or canonical IDs
            team_id: Workspace team ID
            relationship_types: Optional filter by edge types
            max_results: Maximum results to return

        Returns:
            List of related nodes with relationship info
        """
        try:
            # Find entity nodes
            entity_nodes = []
            for entity in entities:
                # Try as canonical ID first
                node = await self._get_node(entity, team_id)
                if not node:
                    # Try as entity name
                    result = await self.db.execute(
                        select(CrossSourceNode).where(
                            and_(
                                CrossSourceNode.team_id == team_id,
                                CrossSourceNode.source == 'derived',
                                or_(
                                    CrossSourceNode.title.ilike(f'%{entity}%'),
                                    CrossSourceNode.canonical_id.ilike(f'%{entity}%')
                                )
                            )
                        ).limit(1)
                    )
                    node = result.scalar_one_or_none()

                if node:
                    entity_nodes.append(node)

            if not entity_nodes:
                return []

            # Get all edges from these entities
            related_nodes = {}  # canonical_id -> (node, score, relationships)

            for entity_node in entity_nodes:
                # Get outbound edges
                outbound = await self.graph_service.link_service.get_outbound_edges(
                    entity_node.canonical_id,
                    team_id,
                    edge_types=relationship_types
                )

                # Get inbound edges
                inbound = await self.graph_service.link_service.get_inbound_edges(
                    entity_node.canonical_id,
                    team_id,
                    edge_types=relationship_types
                )

                # Process edges
                for edge in outbound + inbound:
                    target_id = (
                        edge.target_node_id if edge.source_node_id == entity_node.canonical_id
                        else edge.source_node_id
                    )

                    # Skip entity nodes themselves
                    if target_id in [en.canonical_id for en in entity_nodes]:
                        continue

                    # Load target node
                    target = await self._get_node(target_id, team_id)
                    if not target:
                        continue

                    # Add or update
                    if target_id in related_nodes:
                        _, score, rels = related_nodes[target_id]
                        score += edge.confidence
                        rels.append({
                            'edge_type': edge.edge_type.value,
                            'confidence': edge.confidence,
                            'from_entity': entity_node.title
                        })
                        related_nodes[target_id] = (target, score, rels)
                    else:
                        related_nodes[target_id] = (target, edge.confidence, [{
                            'edge_type': edge.edge_type.value,
                            'confidence': edge.confidence,
                            'from_entity': entity_node.title
                        }])

            # Convert to result format and sort by score
            results = []
            for node, score, relationships in related_nodes.values():
                result = self._node_to_dict(node)
                result['score'] = score
                result['relationships'] = relationships
                result['retrieval_method'] = 'entity_relationships'
                results.append(result)

            results.sort(key=lambda x: x['score'], reverse=True)

            logger.info(
                "entity_relationship_retrieval",
                entities=entities,
                results_count=len(results)
            )

            return results[:max_results]

        except Exception as e:
            logger.error("retrieve_by_entity_relationships_error", error=str(e))
            return []

    async def temporal_graph_search(
        self,
        query: str,
        team_id: str,
        time_range_days: int = 30,
        max_results: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Search with temporal graph analysis

        Finds nodes created around the same time with shared connections.

        Use case: "What happened during the authentication incident?"
        - Finds Slack discussions, GitHub commits, issues from that period
        - Links them via shared entities and temporal proximity

        Args:
            query: Search query
            team_id: Workspace team ID
            time_range_days: Time window to search
            max_results: Maximum results

        Returns:
            List of temporally related nodes
        """
        try:
            # First, do vector search to get initial matches
            # (This would integrate with your existing vector search)

            # For now, demonstrate temporal clustering
            cutoff_date = datetime.utcnow() - timedelta(days=time_range_days)

            # Get recent nodes
            result = await self.db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.created_at >= cutoff_date
                    )
                ).order_by(CrossSourceNode.created_at.desc()).limit(100)
            )
            recent_nodes = result.scalars().all()

            # Cluster by time windows (e.g., 1-day windows)
            time_clusters = {}
            for node in recent_nodes:
                if not node.created_at:
                    continue

                # Round to day
                day_key = node.created_at.strftime('%Y-%m-%d')

                if day_key not in time_clusters:
                    time_clusters[day_key] = []

                time_clusters[day_key].append(node)

            # Find clusters with strong interconnections
            cluster_scores = {}
            for day, nodes in time_clusters.items():
                if len(nodes) < 2:
                    continue

                # Count edges within cluster
                edge_count = 0
                for node in nodes:
                    edges = await self.graph_service.link_service.get_outbound_edges(
                        node.canonical_id,
                        team_id
                    )

                    # Count edges to other nodes in same cluster
                    cluster_ids = {n.canonical_id for n in nodes}
                    edge_count += sum(1 for e in edges if e.target_node_id in cluster_ids)

                # Score cluster
                cluster_scores[day] = {
                    'nodes': nodes,
                    'edge_density': edge_count / len(nodes) if nodes else 0,
                    'size': len(nodes)
                }

            # Return top clusters
            sorted_clusters = sorted(
                cluster_scores.items(),
                key=lambda x: x[1]['edge_density'] * x[1]['size'],
                reverse=True
            )

            results = []
            for day, cluster_data in sorted_clusters[:5]:  # Top 5 time periods
                for node in cluster_data['nodes']:
                    result = self._node_to_dict(node)
                    result['temporal_cluster'] = day
                    result['cluster_density'] = cluster_data['edge_density']
                    result['score'] = cluster_data['edge_density']
                    results.append(result)

            return results[:max_results]

        except Exception as e:
            logger.error("temporal_graph_search_error", error=str(e))
            return []

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

    def _node_to_dict(self, node: CrossSourceNode) -> Dict[str, Any]:
        """Convert node to dict"""
        return {
            'canonical_id': node.canonical_id,
            'source': node.source,
            'source_type': node.source,
            'node_type': node.node_type.value if node.node_type else 'unknown',
            'title': node.title,
            'content': node.content or '',
            'url': node.url,
            'author': node.author,
            'created_at': node.created_at.isoformat() if node.created_at else None,
            'timestamp': str(node.created_at) if node.created_at else None
        }

    def _edge_to_dict(self, edge: CrossSourceEdge) -> Dict[str, Any]:
        """Convert edge to dict"""
        return {
            'source_node_id': edge.source_node_id,
            'target_node_id': edge.target_node_id,
            'edge_type': edge.edge_type.value,
            'confidence': edge.confidence,
            'detection_method': edge.detection_method.value
        }

    def _diversify_results(
        self,
        results: List[Dict[str, Any]],
        max_per_source: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Diversify results across sources

        Prevents all results from being from one source
        """
        source_counts = {}
        diversified = []

        # Sort by score first
        sorted_results = sorted(results, key=lambda x: x.get('score', 0), reverse=True)

        for result in sorted_results:
            source = result.get('source', 'unknown')

            count = source_counts.get(source, 0)
            if count < max_per_source:
                diversified.append(result)
                source_counts[source] = count + 1

        return diversified


def get_graph_enhanced_retrieval(db: AsyncSession) -> GraphEnhancedRetrieval:
    """Get graph-enhanced retrieval instance"""
    return GraphEnhancedRetrieval(db)
