"""Bot interaction orchestration service"""
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.slack_client import slack_client_manager
from app.services.response_formatter import response_formatter
from app.services.retrieval_service import retrieval_service
from app.services.query_service import query_service
from app.services.query_rewriter import query_rewriter
from app.services.llm_service import llm_service
from app.services.conversation_service import conversation_service
from app.services.context_service import context_service
from app.services.prompt_service import prompt_service
from app.services.citation_service import citation_service
from app.core.logging import get_logger
from app.core.monitoring import retrieval_no_results, retrieval_low_confidence
import uuid

logger = get_logger(__name__)


class BotInteractionService:
    """Orchestrates bot interactions and responses"""

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

            # Add eyes reaction to show bot is processing
            await slack_client_manager.add_reaction(
                channel=channel,
                timestamp=thread_ts,
                reaction="eyes"
            )

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

            # Step 2: Rewrite query with conversation context
            original_query = query
            rewritten_query = await query_rewriter.rewrite_query(
                query=query,
                conversation_id=conversation_id,
                db=db
            )
            query_was_rewritten = (rewritten_query != original_query)

            # Refresh conversation after rewriting
            if not conversation:
                conversation = await conversation_service.get_conversation(
                    conversation_id, db
                )

            # Step 3: Analyze rewritten query
            analysis = await query_service.analyze_query(rewritten_query)

            # Step 4: Retrieve relevant context using rewritten query
            retrieval_results = await retrieval_service.retrieve(
                query=rewritten_query,
                query_analysis=analysis,
                user_id=user_id,
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

            # Step 10: Post response
            await slack_client_manager.post_message(
                channel=channel,
                text=formatted_response["text"],
                blocks=formatted_response["blocks"],
                thread_ts=thread_ts
            )

            # Step 11: Get user's team_id for query logging
            from app.models.user import User
            from sqlalchemy import select as sql_select

            user_stmt = sql_select(User).where(User.user_id == user_id)
            user_result = await db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            team_id = user.team_id if user else None

            # Step 12: Log query with enhanced metadata
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

            # Post error message
            error_response = response_formatter.format_error_message(
                "Sorry, I encountered an error while processing your request. Please try again."
            )
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
            # Add eyes reaction to show bot is processing
            await slack_client_manager.add_reaction(
                channel=channel,
                timestamp=thread_ts,
                reaction="eyes"
            )

            # Step 1: Analyze query
            analysis = await query_service.analyze_query(query)

            # Step 2: Retrieve relevant results
            results = await retrieval_service.retrieve(
                query=query,
                query_analysis=analysis,
                user_id=user_id,
                db=db,
                top_k=20
            )

            # Step 3: Format response for Slack
            formatted_response = response_formatter.format_search_results(
                results=results,
                query=query
            )

            # Step 4: Post response
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

            # Post error message
            error_response = response_formatter.format_error_message(
                "Sorry, I encountered an error while searching. Please try again."
            )
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
