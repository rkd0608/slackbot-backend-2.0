"""Bot interaction orchestration service"""
import asyncio
import uuid
from typing import Dict, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.slack_client import slack_client_manager
from app.services.response_formatter import response_formatter
from app.services.retrieval_service import retrieval_service
from app.services.cross_source_retrieval_service import get_cross_source_retrieval_service
from app.services.query_service import query_service
from app.services.query_rewriter import query_rewriter
from app.services.llm_service import llm_service
from app.services.conversation_service import conversation_service
from app.services.conversation_context_service import get_conversation_context_service
from app.services.context_service import context_service
from app.services.prompt_service import prompt_service
from app.services.citation_service import citation_service
from app.services.intelligent_query_service import intelligent_query_service
from app.services.graph_enhanced_retrieval import get_graph_enhanced_retrieval
from app.services.workspace_service import workspace_service
from app.core.logging import get_logger
from app.core.monitoring import retrieval_no_results, retrieval_low_confidence
from app.core.config import settings

logger = get_logger(__name__)


class BotInteractionService:
    """Orchestrates bot interactions and responses"""

    # Thinking messages that rotate while processing (no emojis)
    THINKING_MESSAGES = [
        "Thinking...",
        "Searching your workspace...",
        "Analyzing messages...",
        "Finding relevant discussions...",
        "Processing your request...",
        "Looking through your data...",
        "Gathering context...",
        "Reviewing conversations...",
        "Preparing your answer..."
    ]

    def _get_random_thinking_message(self) -> str:
        """Get a random thinking message"""
        import random
        return random.choice(self.THINKING_MESSAGES)

    def _merge_retrieval_results(
        self,
        slack_results: List[Dict[str, Any]],
        cross_source_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Merge and deduplicate results from Slack-only and cross-source retrieval

        NOW ENHANCED with entity-aware results!
        - Prioritizes entity-based results (more precise)
        - Shows which entities were found
        - Includes related entity content

        Args:
            slack_results: Results from traditional Slack retrieval
            cross_source_data: Results from entity-aware cross-source retrieval

        Returns:
            Combined and deduplicated results with source indicators
        """
        try:
            # Extract results from entity-aware search
            # 'combined_results' already merges entity + semantic results
            cross_source_results = cross_source_data.get('combined_results', [])
            entities_found = cross_source_data.get('entities_found', [])

            # Log which entities were detected
            if entities_found:
                logger.info(
                    "entities_detected_in_query",
                    count=len(entities_found),
                    entities=[e['text'] for e in entities_found]
                )

            # Convert cross-source results to a format compatible with existing pipeline
            # Cross-source results have 'canonical_id', 'source', 'node_type', etc.
            merged = []
            seen_ids = set()

            # Add cross-source results first (higher priority for GitHub code, etc.)
            for result in cross_source_results:
                # Check if this is a Slack message (to deduplicate with slack_results)
                canonical_id = result.get('canonical_id', '')
                source = result.get('source', '')

                # For Slack messages, extract message_id for deduplication
                if source == 'slack':
                    # canonical_id format: "slack:1234567890.123456"
                    message_id = canonical_id.replace('slack:', '')
                    seen_ids.add(message_id)

                # Convert to compatible format
                merged_result = {
                    'canonical_id': canonical_id,
                    'source': source,
                    'source_type': source,  # For display
                    'score': result.get('score', 0.5),
                    'text': result.get('content', ''),
                    'title': result.get('title', ''),
                    'url': result.get('url', ''),
                    'author': result.get('author', 'unknown'),
                    'timestamp': str(result.get('created_at', '')),
                    'node_type': result.get('node_type', 'unknown'),
                    'message_id': canonical_id.replace('slack:', '') if source == 'slack' else None,
                    'channel_name': result.get('channel_context', {}).get('channel_name') if source == 'slack' else None,
                    'channel_id': result.get('channel_context', {}).get('channel_id') if source == 'slack' else None,
                    'user_name': result.get('author', 'unknown'),
                }

                merged.append(merged_result)

            # Add Slack-only results that weren't in cross-source results
            for result in slack_results:
                message_id = result.get('message_id', '')

                # Skip if we already have this from cross-source
                if message_id and message_id in seen_ids:
                    continue

                # Add source indicator for Slack
                result['source'] = 'slack'
                result['source_type'] = 'slack'
                merged.append(result)

            # Sort by score (descending)
            merged.sort(key=lambda x: x.get('score', 0), reverse=True)

            logger.info(
                "retrieval_results_merged",
                slack_count=len(slack_results),
                cross_source_count=len(cross_source_results),
                merged_count=len(merged),
                deduplication_saved=len(slack_results) + len(cross_source_results) - len(merged)
            )

            return merged

        except Exception as e:
            logger.error("merge_retrieval_results_failed", error=str(e))
            # Fallback: return Slack results only
            for result in slack_results:
                result['source'] = 'slack'
                result['source_type'] = 'slack'
            return slack_results

    async def handle_ask_query(
        self,
        query: str,
        user_id: str,
        channel: str,
        thread_ts: str,
        db: AsyncSession,
        conversation_id: Optional[str] = None
    ) -> bool:
        """Handle ask query - generate AI answer with citations"""
        try:
            # Generate query_id for feedback tracking
            query_id = str(uuid.uuid4())

            # Step 0: Get user's team_id and workspace token FIRST (needed for posting messages)
            from app.models.user import User
            from sqlalchemy import select as sql_select

            user_stmt = sql_select(User).where(User.user_id == user_id)
            user_result = await db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            team_id = user.team_id if user else None

            if not team_id:
                logger.error("user_team_id_not_found", user_id=user_id)
                raise ValueError(f"Could not find team_id for user {user_id}")

            # Get workspace token for posting messages
            workspace_token = await workspace_service.get_workspace_token(team_id, db)

            # Post initial thinking message with workspace token
            thinking_message = self._get_random_thinking_message()
            thinking_response = await slack_client_manager.post_message(
                channel=channel,
                text=thinking_message,
                thread_ts=thread_ts,
                bot_token=workspace_token
            )
            thinking_ts = thinking_response.get("ts") if thinking_response else None

            # Step 1: Get or create conversation based on thread
            # Always use thread-based conversations to maintain context across slash commands and messages
            conversation = None
            if conversation_id:
                conversation = await conversation_service.get_conversation(
                    conversation_id, db
                )
            else:
                # Create thread-based conversation using thread_ts
                conversation_id = await conversation_service.get_or_create_by_thread(
                    thread_ts=thread_ts,
                    user_id=user_id,
                    initial_query=query,
                    db=db
                )

            # Step 2a: Check if bot is ready to answer queries (team_id already fetched in Step 0)
            from app.services.bot_readiness_service import get_bot_readiness_service
            readiness_service = get_bot_readiness_service(db)
            is_ready = await readiness_service.is_bot_ready(team_id)

            if not is_ready:
                # Bot not ready - update thinking message with status
                not_ready_message = await readiness_service.get_not_ready_message(team_id)

                # Update thinking message with bot not ready status
                if thinking_ts:
                    await slack_client_manager.update_message(
                        channel=channel,
                        timestamp=thinking_ts,
                        text=not_ready_message,
                        bot_token=workspace_token
                    )
                else:
                    # Fallback: post new message if thinking message failed
                    await slack_client_manager.post_message(
                        channel=channel,
                        text=not_ready_message,
                        thread_ts=thread_ts
                    )

                logger.info(
                    "query_blocked_bot_not_ready",
                    team_id=team_id,
                    user_id=user_id,
                    query=query
                )

                return False  # Query not processed

            # Step 2b: Expand query with team-specific vocabulary
            # Automatically expands abbreviations: "k8s" -> "kubernetes", "FE" -> "frontend"
            from app.services.team_vocabulary_service import get_team_vocabulary_service
            vocab_service = get_team_vocabulary_service(db)
            vocab_expansion = await vocab_service.expand_query(team_id, query)

            if vocab_expansion['expansions']:
                logger.info(
                    "query_expanded_with_vocabulary",
                    original=query,
                    expanded=vocab_expansion['expanded_query'],
                    expansions=[e['term'] for e in vocab_expansion['expansions']]
                )
                query = vocab_expansion['expanded_query']  # Use expanded query

            # Step 2c: Track conversation context with structured entity tracking
            context_service_instance = get_conversation_context_service(db)
            context_conversation = await context_service_instance.get_or_create_conversation(
                user_id=user_id,
                team_id=team_id,
                channel_id=channel
            )

            # Step 2d: Apply entity-based context rewriting (fast, rule-based)
            context_rewrite_result = await context_service_instance.rewrite_query_with_context(
                query=query,
                conversation_id=str(context_conversation.id)
            )

            # Step 2e: Rewrite query with LLM-based conversation context (slower, more sophisticated)
            original_query = query
            rewritten_query = await query_rewriter.rewrite_query(
                query=context_rewrite_result['rewritten_query'],  # Use entity-enhanced query
                conversation_id=conversation_id,
                db=db
            )
            query_was_rewritten = (rewritten_query != original_query)

            if query_was_rewritten:
                logger.info(
                    "query_rewritten",
                    original=original_query,
                    entity_rewrite=context_rewrite_result['rewritten_query'],
                    final_rewrite=rewritten_query,
                    context_entities=context_rewrite_result.get('context_entities', [])
                )

            # Refresh conversation after rewriting
            if not conversation:
                conversation = await conversation_service.get_conversation(
                    conversation_id, db
                )

            # Step 3: Analyze rewritten query
            analysis = await query_service.analyze_query(rewritten_query)

            # Step 3b: Add query to context for future queries
            await context_service_instance.add_query_to_context(
                conversation_id=str(context_conversation.id),
                query=original_query,
                query_analysis=analysis
            )

            # Step 5: INTELLIGENT RETRIEVAL with all advanced features
            # Uses: Query Decomposition, Learned Rewrites, Graph-Enhanced Retrieval
            try:
                # Check if intelligent query service is enabled
                use_intelligent_service = getattr(settings, 'enable_intelligent_query', True)

                if use_intelligent_service:
                    # Use intelligent query service with all advanced features:
                    # - Query decomposition for complex queries
                    # - Learned rewrites from user feedback
                    # - Graph-enhanced retrieval for entity connections
                    # - Cross-source search
                    logger.info("using_intelligent_query_service", query=rewritten_query)

                    intelligent_result = await intelligent_query_service.process_query(
                        query=rewritten_query,
                        user_id=user_id,
                        team_id=team_id,
                        conversation_id=conversation_id,
                        db=db
                    )

                    retrieval_results = intelligent_result['results']
                    processing_metadata = intelligent_result.get('metadata', {})

                    # Log what the intelligent service did
                    if processing_metadata:
                        logger.info(
                            "intelligent_query_metadata",
                            was_rewritten=processing_metadata.get('was_rewritten', False),
                            was_decomposed=processing_metadata.get('was_decomposed', False),
                            steps=len(processing_metadata.get('steps', []))
                        )

                else:
                    # Simple retrieval: PROVEN, WORKING retrieval service
                    # Queries Message table directly - no cross-source complexity
                    logger.info("using_simple_retrieval", query=rewritten_query)

                    # Use proven retrieval service that directly queries Message table
                    retrieval_results = await retrieval_service.retrieve(
                        query=rewritten_query,
                        query_analysis=analysis,
                        user_id=user_id,
                        team_id=team_id,
                        db=db,
                        top_k=15  # Get more results since we're not merging sources
                    )

                    logger.info(
                        "simple_retrieval_completed",
                        results_count=len(retrieval_results)
                    )

            except Exception as e:
                logger.error("hybrid_retrieval_error", error=str(e))
                # Fallback to Slack-only retrieval
                retrieval_results = await retrieval_service.retrieve(
                    query=rewritten_query,
                    query_analysis=analysis,
                    user_id=user_id,
                    team_id=team_id,
                    db=db,
                    top_k=10
                )

            # Extract retrieval metadata
            retrieval_metadata = self._extract_retrieval_metadata(retrieval_results)

            # Track retrieval quality metrics
            self._track_retrieval_metrics(retrieval_results, analysis)

            # Step 5: Get conversation history
            conversation_history = []
            if conversation:
                conversation_history = conversation.history if hasattr(conversation, 'history') else conversation.get('history', [])

            logger.info(
                "conversation_history_retrieved",
                conversation_id=conversation_id,
                has_conversation=bool(conversation),
                history_length=len(conversation_history),
                history_preview=str(conversation_history)[:500] if conversation_history else "empty"
            )

            # Step 6: Assemble context
            context_data = await context_service.assemble_context(
                results=retrieval_results,  # retrieval_results is already a list
                query_analysis=analysis,
                db=db,
                max_messages=50
            )

            # Step 7: Build prompt (use original query for prompt, not rewritten)
            prompt = prompt_service.build_prompt(
                query=query,  # Use original query so user sees their actual question
                query_analysis=analysis,
                context=context_data
            )

            # Step 8: Generate LLM response
            if conversation_history:
                logger.info(
                    "using_conversation_history_for_llm",
                    conversation_id=conversation_id,
                    history_turns=len(conversation_history),
                    history_summary=[{"role": h.get("role"), "content_length": len(h.get("content", ""))} for h in conversation_history]
                )
                llm_response = await llm_service.generate_with_conversation(
                    prompt=prompt,
                    conversation_history=conversation_history,
                    stream=False
                )
            else:
                logger.info(
                    "no_conversation_history_for_llm",
                    conversation_id=conversation_id,
                    reason="history is empty"
                )
                llm_response = await llm_service.generate_response(
                    prompt=prompt,
                    stream=False
                )

            answer_text = llm_response["content"]

            # Step 7: Extract citations
            processed_answer, citations = citation_service.extract_citations(answer_text)
            formatted_citations = citation_service.format_citations(
                citations,
                context_data.get("thread_contexts", [])
            )

            # Step 8: Update conversation - add user query
            await conversation_service.add_turn(
                conversation_id=conversation_id,
                role="user",
                content=query,
                db=db
            )

            # Add assistant response
            await conversation_service.add_turn(
                conversation_id=conversation_id,
                role="assistant",
                content=processed_answer,
                db=db
            )

            # Step 9: Format response for Slack with feedback buttons
            formatted_response = response_formatter.format_answer_response(
                answer=processed_answer,
                citations=formatted_citations,
                confidence=None,
                query_id=query_id
            )

            # Step 10: Post response (using workspace-specific token fetched in Step 0)
            # Update the thinking message with the actual response
            if thinking_ts:
                await slack_client_manager.update_message(
                    channel=channel,
                    timestamp=thinking_ts,
                    text=formatted_response["text"],
                    blocks=formatted_response["blocks"],
                    bot_token=workspace_token
                )
            else:
                # Fallback: post new message if thinking message failed
                await slack_client_manager.post_message(
                    channel=channel,
                    text=formatted_response["text"],
                    blocks=formatted_response["blocks"],
                    thread_ts=thread_ts,
                    bot_token=workspace_token
                )

            # Step 11: Log query with enhanced metadata (team_id already fetched in Step 3)
            await self._log_query(
                query_id=query_id,
                user_id=user_id,
                team_id=team_id,
                original_query=original_query,
                rewritten_query=rewritten_query if query_was_rewritten else None,
                query_was_rewritten=query_was_rewritten,
                analysis=analysis,
                retrieval_metadata=retrieval_metadata,
                response=processed_answer,
                citations=formatted_citations,
                db=db
            )

            logger.info(
                "ask_query_handled",
                user_id=user_id,
                channel=channel,
                conversation_id=conversation_id,
                query_id=query_id
            )

            return True

        except Exception as e:
            logger.error("handle_ask_error", error=str(e), user_id=user_id)

            # Update thinking message with error
            error_response = response_formatter.format_error_message(
                "Sorry, I encountered an error while processing your request. Please try again."
            )

            if thinking_ts:
                await slack_client_manager.update_message(
                    channel=channel,
                    timestamp=thinking_ts,
                    text=error_response["text"],
                    blocks=error_response["blocks"],
                    bot_token=workspace_token
                )
            else:
                # Fallback: post new message if thinking message failed
                await slack_client_manager.post_message(
                    channel=channel,
                    text=error_response["text"],
                    blocks=error_response["blocks"],
                    thread_ts=thread_ts
                )

            return False

    async def handle_find_query(
        self,
        query: str,
        user_id: str,
        channel: str,
        thread_ts: str,
        db: AsyncSession
    ) -> bool:
        """Handle find query - search and return results"""
        try:
            # Step 1: Get user's team_id and workspace token FIRST
            from app.models.user import User
            from sqlalchemy import select as sql_select

            user_stmt = sql_select(User).where(User.user_id == user_id)
            user_result = await db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            team_id = user.team_id if user else None

            if not team_id:
                logger.error("user_team_id_not_found", user_id=user_id)
                raise ValueError(f"Could not find team_id for user {user_id}")

            # Get workspace token for posting messages
            workspace_token = await workspace_service.get_workspace_token(team_id, db)

            # Post initial thinking message with workspace token
            thinking_message = self._get_random_thinking_message()
            thinking_response = await slack_client_manager.post_message(
                channel=channel,
                text=thinking_message,
                thread_ts=thread_ts,
                bot_token=workspace_token
            )
            thinking_ts = thinking_response.get("ts") if thinking_response else None

            # Step 2: Analyze query (team_id already fetched in Step 1)
            analysis = await query_service.analyze_query(query)

            # Step 3: Retrieve relevant results WITH PERMISSION FILTERING
            results = await retrieval_service.retrieve(
                query=query,
                query_analysis=analysis,
                user_id=user_id,
                team_id=team_id,  # ✅ CRITICAL: Pass team_id for permission filtering
                db=db,
                top_k=20
            )

            # Step 3: Format response for Slack
            formatted_response = response_formatter.format_search_results(
                results=results,
                query=query
            )

            # Step 4: Update thinking message with results
            if thinking_ts:
                await slack_client_manager.update_message(
                    channel=channel,
                    timestamp=thinking_ts,
                    text=formatted_response["text"],
                    blocks=formatted_response["blocks"],
                    bot_token=workspace_token
                )
            else:
                # Fallback: post new message if thinking message failed
                await slack_client_manager.post_message(
                    channel=channel,
                    text=formatted_response["text"],
                    blocks=formatted_response["blocks"],
                    thread_ts=thread_ts
                )

            logger.info(
                "find_query_handled",
                user_id=user_id,
                channel=channel,
                result_count=len(results)
            )

            return True

        except Exception as e:
            logger.error("handle_find_error", error=str(e), user_id=user_id)

            # Update thinking message with error
            error_response = response_formatter.format_error_message(
                "Sorry, I encountered an error while searching. Please try again."
            )

            if thinking_ts:
                await slack_client_manager.update_message(
                    channel=channel,
                    timestamp=thinking_ts,
                    text=error_response["text"],
                    blocks=error_response["blocks"],
                    bot_token=workspace_token
                )
            else:
                # Fallback: post new message if thinking message failed
                await slack_client_manager.post_message(
                    channel=channel,
                    text=error_response["text"],
                    blocks=error_response["blocks"],
                    thread_ts=thread_ts
                )

            return False

    async def handle_message_event(
        self,
        event: Dict[str, Any],
        db: AsyncSession
    ) -> bool:
        """Handle app_mention or message.im event"""
        try:
            user_id = event.get("user")
            channel = event.get("channel")
            text = event.get("text", "").strip()
            thread_ts = event.get("thread_ts") or event.get("ts")

            # Remove bot mention from text if present
            # Bot mentions look like <@U123456789>
            import re
            original_text = text
            text = re.sub(r'<@[A-Z0-9]+>', '', text).strip()

            logger.info(
                "message_event_debug",
                user_id=user_id,
                channel=channel,
                original_text=original_text,
                text_after_mention_removal=text,
                thread_ts=thread_ts
            )

            if not text:
                # If no text after removing mention, ignore (don't show help)
                # This prevents duplicate responses when slash commands trigger mention events
                logger.info("empty_mention_ignored", user=user_id, channel=channel)
                return True

            # Get or create conversation for this thread to maintain context
            conversation_id = await conversation_service.get_or_create_by_thread(
                thread_ts=thread_ts,
                user_id=user_id,
                initial_query=text,
                db=db
            )

            logger.info(
                "conversation_linked_to_thread",
                conversation_id=conversation_id,
                thread_ts=thread_ts,
                user_id=user_id
            )

            # Always treat natural language conversations as ask queries (AI-generated answers)
            # This enables multi-turn conversations in threads
            # Users should use /find explicitly if they want search results
            return await self.handle_ask_query(
                query=text,
                user_id=user_id,
                channel=channel,
                thread_ts=thread_ts,
                db=db,
                conversation_id=conversation_id  # Pass conversation_id to maintain context
            )

        except Exception as e:
            logger.error("handle_message_event_error", error=str(e))
            return False

    def _extract_retrieval_metadata(self, retrieval_results: list) -> dict:
        """Extract metadata from retrieval results for logging"""
        if not retrieval_results:
            return {
                "scores": [],
                "sources": [],
                "top_score": None,
                "avg_score": None
            }

        scores = [r.get("score", 0) for r in retrieval_results]
        sources = [r.get("source", "unknown") for r in retrieval_results]

        return {
            "scores": scores,
            "sources": sources,
            "top_score": max(scores) if scores else None,
            "avg_score": sum(scores) / len(scores) if scores else None
        }

    def _track_retrieval_metrics(self, retrieval_results: list, analysis: dict):
        """Track retrieval quality metrics in Prometheus"""
        query_type = analysis.get("query_type", "unknown")

        # Track no results
        if not retrieval_results:
            retrieval_no_results.labels(query_type=query_type).inc()
            return

        # Track low confidence
        top_score = retrieval_results[0].get("score", 0) if retrieval_results else 0

        if top_score < 0.3:
            retrieval_low_confidence.labels(threshold="0.3").inc()
        if top_score < 0.5:
            retrieval_low_confidence.labels(threshold="0.5").inc()
        if top_score < 0.7:
            retrieval_low_confidence.labels(threshold="0.7").inc()

    async def _log_query(
        self,
        query_id: str,
        user_id: str,
        team_id: Optional[str],
        original_query: str,
        rewritten_query: str,
        query_was_rewritten: bool,
        analysis: dict,
        retrieval_metadata: dict,
        response: str,
        citations: list,
        db: AsyncSession
    ):
        """Log query with enhanced metadata"""
        from app.models.query_log import QueryLog

        try:
            query_log = QueryLog(
                query_id=query_id,
                user_id=user_id,
                team_id=team_id,
                query_text=rewritten_query or original_query,
                original_query=original_query,
                rewritten_query=rewritten_query,
                query_was_rewritten=1 if query_was_rewritten else 0,
                intent_type=analysis.get("query_type"),
                entities_extracted=analysis.get("entities"),
                channel_filters=analysis.get("channels"),
                results_count=len(citations),
                result_message_ids=[c.get("message_id") for c in citations if c.get("message_id")],
                retrieval_scores=retrieval_metadata.get("scores"),
                retrieval_sources=retrieval_metadata.get("sources"),
                top_result_score=retrieval_metadata.get("top_score"),
                avg_result_score=retrieval_metadata.get("avg_score"),
                response_text=response,
                citations=citations
            )

            db.add(query_log)
            await db.commit()

            logger.info("query_logged", query_id=query_id, user_id=user_id, team_id=team_id)

        except Exception as e:
            logger.error("query_log_error", error=str(e), query_id=query_id)
            await db.rollback()


# Global bot interaction service instance
bot_interaction_service = BotInteractionService()
