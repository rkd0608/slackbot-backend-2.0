"""
Intelligent Query Service

Orchestrates the complete query intelligence pipeline:
1. Query Analysis
2. Learned Rewrites
3. Query Decomposition
4. Cross-Source Planning
5. Result Merging

This is the "smart layer" on top of existing retrieval services.
"""
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.query_service import query_service
from app.services.query_decomposer import query_decomposer
from app.services.query_rewrite_learner import get_rewrite_learner
from app.services.query_rewriter import query_rewriter
from app.services.retrieval_service import retrieval_service
from app.services.cross_source_retrieval_service import get_cross_source_retrieval_service

logger = get_logger(__name__)


class IntelligentQueryService:
    """
    Orchestrates intelligent query processing with learning capabilities

    Flow:
    1. Analyze query (intent, entities, complexity)
    2. Apply learned rewrites (from feedback data)
    3. Apply conversation context rewrites
    4. Decompose if needed (complex multi-part queries)
    5. Execute sub-queries with optimal strategies
    6. Merge and rank results
    7. Track for future learning
    """

    async def process_query(
        self,
        query: str,
        user_id: str,
        team_id: str,
        conversation_id: Optional[str] = None,
        db: AsyncSession = None
    ) -> Dict[str, Any]:
        """
        Process query with full intelligence pipeline

        Args:
            query: User's query
            user_id: User ID
            team_id: Workspace team ID
            conversation_id: Optional conversation for context
            db: Database session

        Returns:
            Dict with:
            - final_query: str (after all rewrites)
            - results: List[Dict] (retrieved results)
            - metadata: Dict (processing stats)
        """
        try:
            processing_metadata = {
                'original_query': query,
                'steps': []
            }

            # Step 1: Analyze query
            logger.info("intelligent_query_processing_started", query=query, team_id=team_id)

            analysis = await query_service.analyze_query(query)
            processing_metadata['steps'].append({
                'step': 'analyze',
                'intents': analysis.get('intents', []),
                'entities': len(analysis.get('entities', []))
            })

            # Step 2: Apply learned rewrites
            rewrite_learner = get_rewrite_learner(db)
            learned_rewrite = await rewrite_learner.apply_learned_rewrites(
                query=query,
                team_id=team_id,
                query_intent=analysis.get('intents', [None])[0]
            )

            rewritten_query = learned_rewrite['rewritten_query']
            if learned_rewrite['rules_applied']:
                processing_metadata['steps'].append({
                    'step': 'learned_rewrite',
                    'rules_applied': len(learned_rewrite['rules_applied']),
                    'confidence': learned_rewrite['confidence']
                })
                logger.info(
                    "learned_rewrites_applied",
                    original=query,
                    rewritten=rewritten_query
                )

            # Step 3: Apply conversation context rewriting
            if conversation_id:
                context_rewritten = await query_rewriter.rewrite_query(
                    query=rewritten_query,
                    conversation_id=conversation_id,
                    db=db
                )

                if context_rewritten != rewritten_query:
                    processing_metadata['steps'].append({
                        'step': 'context_rewrite',
                        'added_context': True
                    })
                    rewritten_query = context_rewritten

            # Step 4: Check if decomposition needed
            decomposition = await query_decomposer.decompose_query(
                query=rewritten_query,
                query_analysis=analysis
            )

            if decomposition['needs_decomposition']:
                processing_metadata['steps'].append({
                    'step': 'decomposition',
                    'sub_queries': len(decomposition['sub_queries']),
                    'reason': decomposition.get('decomposition_reason')
                })

                # Execute sub-queries
                results = await self._execute_decomposed_query(
                    decomposition=decomposition,
                    user_id=user_id,
                    team_id=team_id,
                    db=db
                )

            else:
                # Single query execution
                processing_metadata['steps'].append({
                    'step': 'single_query_execution'
                })

                results = await self._execute_single_query(
                    query=rewritten_query,
                    analysis=analysis,
                    user_id=user_id,
                    team_id=team_id,
                    db=db
                )

            # Add processing metadata to results
            for result in results[:20]:  # Limit to top 20
                result['query_processing'] = {
                    'was_rewritten': rewritten_query != query,
                    'was_decomposed': decomposition['needs_decomposition'],
                    'learned_rules_applied': len(learned_rewrite.get('rules_applied', []))
                }

            logger.info(
                "intelligent_query_completed",
                original=query,
                final=rewritten_query,
                results_count=len(results),
                processing_steps=len(processing_metadata['steps'])
            )

            return {
                'final_query': rewritten_query,
                'results': results,
                'metadata': processing_metadata
            }

        except Exception as e:
            logger.error("intelligent_query_processing_error", error=str(e), query=query)
            # Fallback to simple retrieval
            results = await retrieval_service.retrieve(
                query=query,
                query_analysis=analysis,
                user_id=user_id,
                team_id=team_id,
                db=db,
                top_k=20
            )

            return {
                'final_query': query,
                'results': results,
                'metadata': {'error': str(e), 'fallback': True}
            }

    async def _execute_decomposed_query(
        self,
        decomposition: Dict[str, Any],
        user_id: str,
        team_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """
        Execute decomposed query by running sub-queries in parallel

        Args:
            decomposition: Decomposition result with sub-queries
            user_id: User ID
            team_id: Team ID
            db: Database session

        Returns:
            Merged results from all sub-queries
        """
        import asyncio

        sub_queries = decomposition['sub_queries']

        # Execute sub-queries in parallel
        tasks = []
        for sq in sub_queries:
            task = self._execute_sub_query(
                sub_query=sq,
                user_id=user_id,
                team_id=team_id,
                db=db
            )
            tasks.append(task)

        sub_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions
        valid_results = []
        for i, result in enumerate(sub_results):
            if isinstance(result, Exception):
                logger.error(
                    "sub_query_failed",
                    sub_query=sub_queries[i].get('query'),
                    error=str(result)
                )
            else:
                valid_results.append({
                    'query': sub_queries[i].get('query'),
                    'weight': sub_queries[i].get('weight', 1.0),
                    'results': result
                })

        # Merge results
        merged = await query_decomposer.merge_results(
            sub_query_results=valid_results,
            original_query=decomposition['original_query']
        )

        return merged

    async def _execute_sub_query(
        self,
        sub_query: Dict[str, Any],
        user_id: str,
        team_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """
        Execute a single sub-query with specified strategy

        Args:
            sub_query: Sub-query dict with query, source_preference, search_strategy
            user_id: User ID
            team_id: Team ID
            db: Database session

        Returns:
            Results for this sub-query
        """
        query_text = sub_query['query']
        source_pref = sub_query.get('source_preference', 'all')
        strategy = sub_query.get('search_strategy', 'hybrid')

        logger.info(
            "executing_sub_query",
            query=query_text,
            source=source_pref,
            strategy=strategy
        )

        # Analyze sub-query
        analysis = await query_service.analyze_query(query_text)

        # Choose retrieval method based on source preference
        if source_pref == 'github' or source_pref == 'slack':
            # Use cross-source retrieval with source filter
            cross_source_service = get_cross_source_retrieval_service()
            results_data = await cross_source_service.entity_aware_search(
                query=query_text,
                team_id=team_id,
                db=db,
                sources=[source_pref],
                top_k=10
            )
            results = results_data.get('combined_results', [])

        else:
            # Use standard retrieval (Slack-focused for now)
            results = await retrieval_service.retrieve(
                query=query_text,
                query_analysis=analysis,
                user_id=user_id,
                team_id=team_id,
                db=db,
                top_k=10
            )

        return results

    async def _execute_single_query(
        self,
        query: str,
        analysis: Dict[str, Any],
        user_id: str,
        team_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """
        Execute single query with standard hybrid retrieval

        Args:
            query: Query text
            analysis: Query analysis
            user_id: User ID
            team_id: Team ID
            db: Database session

        Returns:
            Retrieved results
        """
        # Use hybrid retrieval (Slack + cross-source)
        import asyncio

        # Run both in parallel
        slack_task = retrieval_service.retrieve(
            query=query,
            query_analysis=analysis,
            user_id=user_id,
            team_id=team_id,
            db=db,
            top_k=15
        )

        cross_source_service = get_cross_source_retrieval_service()
        cross_task = cross_source_service.entity_aware_search(
            query=query,
            team_id=team_id,
            db=db,
            sources=["slack", "github"],
            top_k=15
        )

        slack_results, cross_results = await asyncio.gather(
            slack_task,
            cross_task,
            return_exceptions=True
        )

        # Handle exceptions
        if isinstance(slack_results, Exception):
            logger.error("slack_retrieval_failed", error=str(slack_results))
            slack_results = []

        if isinstance(cross_results, Exception):
            logger.error("cross_source_retrieval_failed", error=str(cross_results))
            cross_results = {'combined_results': []}

        # Merge results
        all_results = []
        seen_ids = set()

        # Add cross-source results first (often higher quality)
        for result in cross_results.get('combined_results', []):
            result_id = result.get('canonical_id') or result.get('message_id')
            if result_id and result_id not in seen_ids:
                seen_ids.add(result_id)
                all_results.append(result)

        # Add Slack results
        for result in slack_results:
            result_id = result.get('canonical_id') or result.get('message_id')
            if result_id and result_id not in seen_ids:
                seen_ids.add(result_id)
                all_results.append(result)

        # Sort by score
        all_results.sort(key=lambda x: x.get('score', 0), reverse=True)

        return all_results[:20]


# Global intelligent query service instance
intelligent_query_service = IntelligentQueryService()
