"""
Cross-Source Retrieval Service

Extends the base retrieval service to search across all knowledge sources
(Slack, GitHub, Jira, Confluence, etc.) using the unified knowledge graph.
"""
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.core.logging import get_logger
from app.core.vector_db import vector_db_manager
from app.models.cross_source_node import CrossSourceNode, NodeType
from app.services.cross_source_graph_service import get_cross_source_graph_service
from app.services.embedding_service import embedding_service

logger = get_logger(__name__)


class CrossSourceRetrievalService:
    """
    Unified retrieval across all knowledge sources

    This service enables querying across Slack, GitHub, Jira, etc. simultaneously
    and uses the knowledge graph to expand context and find related content.
    """

    async def search_across_sources(
        self,
        query: str,
        team_id: str,
        db: AsyncSession,
        sources: Optional[List[str]] = None,
        node_types: Optional[List[NodeType]] = None,
        expand_graph: bool = True,
        max_depth: int = 2,
        top_k: int = 20
    ) -> Dict[str, Any]:
        """
        Search across all integrated knowledge sources

        Args:
            query: Search query
            team_id: Workspace team ID
            db: Database session
            sources: Optional filter by sources (e.g., ["slack", "github"])
            node_types: Optional filter by node types
            expand_graph: Whether to expand results using graph traversal
            max_depth: Graph traversal depth if expand_graph=True
            top_k: Number of results to return

        Returns:
            Dict with:
                - results: List of nodes matching query
                - expanded_context: Additional related nodes (if expand_graph=True)
                - reasoning: Explanation of how results are related
        """
        try:
            logger.info(
                "cross_source_search_started",
                query=query[:100],
                team_id=team_id,
                sources=sources,
                expand_graph=expand_graph
            )

            # Step 1: Semantic search across all sources
            semantic_results = await self._semantic_search_cross_source(
                query=query,
                team_id=team_id,
                db=db,
                sources=sources,
                node_types=node_types,
                top_k=top_k * 2  # Get more candidates for graph expansion
            )

            # Step 2: Optionally expand using knowledge graph
            expanded_context = []
            reasoning_paths = []

            if expand_graph and semantic_results:
                graph_service = get_cross_source_graph_service(db)

                # Extract seed node IDs from search results
                seed_node_ids = [result['canonical_id'] for result in semantic_results[:10]]

                # Expand context
                expansion = await graph_service.expand_context_from_seed_nodes(
                    seed_node_ids=seed_node_ids,
                    team_id=team_id,
                    max_depth=max_depth,
                    max_nodes=50
                )

                expanded_context = expansion['nodes']
                reasoning_paths = expansion['reasoning_paths']

                logger.info(
                    "graph_expansion_completed",
                    seed_count=len(seed_node_ids),
                    expanded_nodes=len(expanded_context)
                )

            # Step 3: Combine and deduplicate results
            all_results = semantic_results + [
                node for node in expanded_context
                if node['canonical_id'] not in {r['canonical_id'] for r in semantic_results}
            ]

            # Sort by relevance (semantic results have priority)
            for i, result in enumerate(semantic_results):
                result['rank_score'] = result.get('score', 0.5) + (1.0 if i < 10 else 0.5)

            for node in expanded_context:
                if node['canonical_id'] not in {r['canonical_id'] for r in semantic_results}:
                    node['rank_score'] = node.get('relevance_score', 0.3)

            all_results.sort(key=lambda x: x.get('rank_score', 0), reverse=True)

            result = {
                'query': query,
                'results': all_results[:top_k],
                'expanded_context': expanded_context,
                'reasoning_paths': reasoning_paths,
                'stats': {
                    'semantic_results': len(semantic_results),
                    'expanded_nodes': len(expanded_context),
                    'total_unique': len(all_results),
                    'returned': len(all_results[:top_k])
                }
            }

            logger.info(
                "cross_source_search_completed",
                team_id=team_id,
                stats=result['stats']
            )

            return result

        except Exception as e:
            logger.error(
                "cross_source_search_failed",
                query=query,
                team_id=team_id,
                error=str(e)
            )
            raise

    async def _semantic_search_cross_source(
        self,
        query: str,
        team_id: str,
        db: AsyncSession,
        sources: Optional[List[str]] = None,
        node_types: Optional[List[NodeType]] = None,
        top_k: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Perform semantic search across unified knowledge graph

        Uses vector embeddings stored in Pinecone to find semantically similar nodes
        across all sources.
        """
        try:
            # Generate query embedding
            query_embedding = await embedding_service.get_embedding(query)

            # Build metadata filter
            metadata_filter = {'team_id': team_id}

            if sources:
                # Note: Pinecone filters - adjust based on your schema
                metadata_filter['source'] = {'$in': sources}

            if node_types:
                metadata_filter['node_type'] = {'$in': [nt.value for nt in node_types]}

            # Search across namespaces
            # For cross-source search, we'll search both semantic and code namespaces
            results = []

            # Search semantic namespace
            semantic_namespace = f"{team_id}:semantic"
            try:
                semantic_matches = vector_db_manager.query_namespace(
                    namespace=semantic_namespace,
                    vector=query_embedding,
                    top_k=top_k,
                    filter=metadata_filter
                )

                if semantic_matches and 'matches' in semantic_matches:
                    for match in semantic_matches['matches']:
                        canonical_id = match['id'].replace(':semantic', '')
                        results.append({
                            'canonical_id': canonical_id,
                            'score': match['score'],
                            'metadata': match.get('metadata', {}),
                            'embedding_type': 'semantic'
                        })
            except Exception as e:
                logger.warning(
                    "semantic_namespace_search_failed",
                    namespace=semantic_namespace,
                    error=str(e)
                )

            # Search code namespace (for code-specific queries)
            code_namespace = f"{team_id}:code"
            try:
                code_matches = vector_db_manager.query_namespace(
                    namespace=code_namespace,
                    vector=query_embedding,
                    top_k=top_k // 2,  # Fewer code results
                    filter=metadata_filter
                )

                if code_matches and 'matches' in code_matches:
                    for match in code_matches['matches']:
                        canonical_id = match['id'].replace(':code', '')
                        # Avoid duplicates
                        if not any(r['canonical_id'] == canonical_id for r in results):
                            results.append({
                                'canonical_id': canonical_id,
                                'score': match['score'],
                                'metadata': match.get('metadata', {}),
                                'embedding_type': 'code'
                            })
            except Exception as e:
                logger.warning(
                    "code_namespace_search_failed",
                    namespace=code_namespace,
                    error=str(e)
                )

            # Load full node data from database
            if results:
                canonical_ids = [r['canonical_id'] for r in results]

                db_result = await db.execute(
                    select(CrossSourceNode).where(
                        and_(
                            CrossSourceNode.canonical_id.in_(canonical_ids),
                            CrossSourceNode.team_id == team_id,
                            CrossSourceNode.is_deleted == "false"
                        )
                    )
                )
                nodes = {node.canonical_id: node for node in db_result.scalars().all()}

                # Merge vector search results with node data
                enriched_results = []
                for result in results:
                    node = nodes.get(result['canonical_id'])
                    if node:
                        enriched_results.append({
                            **node.to_dict(),
                            'score': result['score'],
                            'embedding_type': result['embedding_type']
                        })

                # Sort by score
                enriched_results.sort(key=lambda x: x['score'], reverse=True)

                return enriched_results[:top_k]

            return []

        except Exception as e:
            logger.error(
                "semantic_search_cross_source_failed",
                query=query,
                error=str(e)
            )
            return []

    async def find_related_to_slack_message(
        self,
        message_id: str,
        team_id: str,
        db: AsyncSession,
        sources: Optional[List[str]] = None,
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Find content related to a Slack message across all sources

        Use case: User clicks on a Slack message → show related GitHub PRs, Jira tickets, etc.

        Args:
            message_id: Slack message ID
            team_id: Team ID
            db: Database session
            sources: Optional filter by sources (e.g., ["github", "jira"])
            max_results: Maximum results

        Returns:
            List of related nodes from other sources
        """
        try:
            graph_service = get_cross_source_graph_service(db)

            # Get Slack message node
            canonical_id = f"slack:{message_id}"

            result = await db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.canonical_id == canonical_id,
                        CrossSourceNode.team_id == team_id
                    )
                )
            )
            slack_node = result.scalar_one_or_none()

            if not slack_node:
                logger.warning(
                    "slack_message_node_not_found",
                    message_id=message_id,
                    team_id=team_id
                )
                return []

            # Find related discussions
            related = await graph_service.find_related_discussions(
                canonical_id=canonical_id,
                team_id=team_id,
                source_types=sources,
                max_results=max_results
            )

            return related

        except Exception as e:
            logger.error(
                "find_related_to_slack_message_failed",
                message_id=message_id,
                error=str(e)
            )
            return []

    async def explain_connection(
        self,
        node_a_id: str,
        node_b_id: str,
        team_id: str,
        db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """
        Explain how two nodes are connected

        Use case: "How is this Slack message related to that GitHub PR?"

        Args:
            node_a_id: First node canonical ID
            node_b_id: Second node canonical ID
            team_id: Team ID
            db: Database session

        Returns:
            Explanation dict or None if not connected
        """
        try:
            graph_service = get_cross_source_graph_service(db)

            explanation = await graph_service.explain_relationship(
                node_a_id=node_a_id,
                node_b_id=node_b_id,
                team_id=team_id,
                max_path_length=3
            )

            return explanation

        except Exception as e:
            logger.error(
                "explain_connection_failed",
                node_a=node_a_id,
                node_b=node_b_id,
                error=str(e)
            )
            return None

    async def find_topic_experts(
        self,
        query: str,
        team_id: str,
        db: AsyncSession,
        max_experts: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find experts on a given topic

        Use case: "Who knows about authentication?" → finds people who have worked on
        authentication-related code, issues, discussions

        Args:
            query: Topic query
            team_id: Team ID
            db: Database session
            max_experts: Maximum experts to return

        Returns:
            List of experts with expertise scores
        """
        try:
            # First find nodes related to the topic
            topic_results = await self._semantic_search_cross_source(
                query=query,
                team_id=team_id,
                db=db,
                top_k=10
            )

            if not topic_results:
                return []

            # Use graph service to find experts
            graph_service = get_cross_source_graph_service(db)

            topic_node_ids = [result['canonical_id'] for result in topic_results]

            experts = await graph_service.find_experts(
                topic_node_ids=topic_node_ids,
                team_id=team_id,
                max_experts=max_experts
            )

            return experts

        except Exception as e:
            logger.error(
                "find_topic_experts_failed",
                query=query,
                error=str(e)
            )
            return []


def get_cross_source_retrieval_service() -> CrossSourceRetrievalService:
    """Get cross-source retrieval service instance"""
    return CrossSourceRetrievalService()
