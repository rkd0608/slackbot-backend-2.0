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
            query_embedding = await embedding_service.generate_embedding(query)

            # Build metadata filter
            metadata_filter = {'team_id': team_id}

            if sources:
                # Note: Pinecone filters - adjust based on your schema
                metadata_filter['source'] = {'$in': sources}

            if node_types:
                metadata_filter['node_type'] = {'$in': [nt.value for nt in node_types]}

            # Search across namespaces
            # For cross-source search, we'll search both messages and code namespaces
            results = []

            # Search messages namespace (Slack messages)
            messages_namespace = f"{team_id}:messages"
            try:
                message_matches = vector_db_manager.query_namespace(
                    namespace=messages_namespace,
                    vector=query_embedding,
                    top_k=top_k,
                    filter_dict=metadata_filter
                )

                if message_matches and 'matches' in message_matches:
                    for match in message_matches['matches']:
                        canonical_id = match['id']
                        results.append({
                            'canonical_id': canonical_id,
                            'score': match['score'],
                            'metadata': match.get('metadata', {}),
                            'embedding_type': 'message'
                        })
            except Exception as e:
                logger.warning(
                    "messages_namespace_search_failed",
                    namespace=messages_namespace,
                    error=str(e)
                )

            # Search code namespace (for code-specific queries)
            code_namespace = f"{team_id}:code"
            try:
                code_matches = vector_db_manager.query_namespace(
                    namespace=code_namespace,
                    vector=query_embedding,
                    top_k=top_k // 2,  # Fewer code results
                    filter_dict=metadata_filter
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

    async def entity_aware_search(
        self,
        query: str,
        team_id: str,
        db: AsyncSession,
        sources: Optional[List[str]] = None,
        top_k: int = 20
    ) -> Dict[str, Any]:
        """
        NEW: Entity-aware cross-source search using unified knowledge graph

        This is the INTELLIGENT search that automatically:
        1. Extracts entities from the query
        2. Finds related entities in the unified graph
        3. Searches content about those entities across ALL sources

        Use case: "authentication code in TypeScript"
        - Extracts entity: "authentication"
        - Finds entity:concept:authentication in graph
        - Searches Slack messages + GitHub code + any other sources
        - Returns EVERYTHING in one go!

        Args:
            query: User's search query
            team_id: Team ID
            db: Database session
            sources: Optional filter by sources
            top_k: Number of results

        Returns:
            Dict with:
                - entity_results: Content found via entity graph
                - semantic_results: Traditional semantic search results
                - entities_found: List of entities detected
                - combined_results: Merged and ranked results
        """
        try:
            logger.info(
                "entity_aware_search_started",
                query=query[:100],
                team_id=team_id
            )

            graph_service = get_cross_source_graph_service(db)

            # Step 1: Extract entities from query using entity_service patterns
            from app.services.entity_service import entity_service
            extracted_entities = entity_service._extract_entities_regex(query)

            entity_results = []
            entities_found = []

            if extracted_entities:
                logger.info(
                    "entities_extracted_from_query",
                    count=len(extracted_entities),
                    entities=[e['text'] for e in extracted_entities]
                )

                # Step 2: For each extracted entity, find it in unified graph
                for entity_data in extracted_entities:
                    entity_text = entity_data['text']
                    entity_type = entity_data['type']

                    # Normalize entity text
                    normalized = entity_service._normalize_entity(entity_text)

                    # Build canonical ID
                    canonical_id = f"entity:{entity_type}:{normalized}"

                    # Check if entity exists in unified graph
                    result = await db.execute(
                        select(CrossSourceNode).where(
                            and_(
                                CrossSourceNode.canonical_id == canonical_id,
                                CrossSourceNode.team_id == team_id,
                                CrossSourceNode.source == "derived"
                            )
                        )
                    )
                    entity_node = result.scalar_one_or_none()

                    if entity_node:
                        entities_found.append({
                            'canonical_id': canonical_id,
                            'text': entity_text,
                            'type': entity_type,
                            'occurrences': entity_node.entity_metadata.get('occurrence_count', 0) if entity_node.entity_metadata else 0
                        })

                        # Step 3: Find ALL content about this entity across sources
                        content = await graph_service.find_content_about_entity(
                            entity_canonical_id=canonical_id,
                            team_id=team_id,
                            source_types=sources,
                            max_results=top_k
                        )

                        entity_results.extend(content)

                        logger.info(
                            "entity_content_found",
                            entity=entity_text,
                            canonical_id=canonical_id,
                            content_count=len(content)
                        )

                        # Step 4: Also find related entities
                        related_entities = await graph_service.find_related_entities(
                            entity_canonical_id=canonical_id,
                            team_id=team_id,
                            min_confidence=0.4,
                            max_results=5
                        )

                        # Step 5: Find content about related entities too
                        for related in related_entities[:3]:  # Top 3 related
                            related_content = await graph_service.find_content_about_entity(
                                entity_canonical_id=related['canonical_id'],
                                team_id=team_id,
                                source_types=sources,
                                max_results=5
                            )
                            entity_results.extend(related_content)

                            logger.debug(
                                "related_entity_content_found",
                                related_entity=related['title'],
                                content_count=len(related_content)
                            )

            # Step 6: Also run traditional semantic search
            semantic_results = await self._semantic_search_cross_source(
                query=query,
                team_id=team_id,
                db=db,
                sources=sources,
                top_k=top_k
            )

            # Step 7: Merge and deduplicate results
            seen_ids = set()
            combined_results = []

            # Prioritize entity-based results (more precise)
            for result in entity_results:
                canonical_id = result.get('canonical_id')
                if canonical_id and canonical_id not in seen_ids:
                    seen_ids.add(canonical_id)
                    result['retrieval_method'] = 'entity_graph'
                    result['rank_score'] = result.get('confidence', 0.8) + 0.2  # Boost entity results
                    combined_results.append(result)

            # Add semantic results
            for result in semantic_results:
                canonical_id = result.get('canonical_id')
                if canonical_id and canonical_id not in seen_ids:
                    seen_ids.add(canonical_id)
                    result['retrieval_method'] = 'semantic'
                    result['rank_score'] = result.get('score', 0.5)
                    combined_results.append(result)

            # Sort by rank score
            combined_results.sort(key=lambda x: x.get('rank_score', 0), reverse=True)

            result = {
                'query': query,
                'entities_found': entities_found,
                'entity_results': entity_results[:top_k],
                'semantic_results': semantic_results[:top_k],
                'combined_results': combined_results[:top_k],
                'stats': {
                    'entities_detected': len(extracted_entities),
                    'entities_in_graph': len(entities_found),
                    'entity_based_results': len(entity_results),
                    'semantic_results': len(semantic_results),
                    'total_unique': len(combined_results),
                    'returned': len(combined_results[:top_k])
                }
            }

            logger.info(
                "entity_aware_search_completed",
                team_id=team_id,
                stats=result['stats']
            )

            return result

        except Exception as e:
            logger.error(
                "entity_aware_search_failed",
                query=query,
                team_id=team_id,
                error=str(e)
            )
            # Fallback to semantic search
            return {
                'query': query,
                'entities_found': [],
                'entity_results': [],
                'semantic_results': await self._semantic_search_cross_source(
                    query=query,
                    team_id=team_id,
                    db=db,
                    sources=sources,
                    top_k=top_k
                ),
                'combined_results': [],
                'stats': {'error': str(e)}
            }


def get_cross_source_retrieval_service() -> CrossSourceRetrievalService:
    """Get cross-source retrieval service instance"""
    return CrossSourceRetrievalService()
