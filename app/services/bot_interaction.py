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
            # Add eyes reaction to show bot is processing
            await slack_client_manager.add_reaction(
                channel=channel,
                timestamp=thread_ts,
                reaction="eyes"
            )

            # Step 1: Get or create conversation (moved up to get conversation_id)
            conversation = None
            if conversation_id:
                conversation = await conversation_service.get_conversation(
                    conversation_id, db
                )
            else:
                conversation_id = await conversation_service.create_conversation(
                    user_id=user_id,
                    initial_query=query,
                    db=db
                )

            # Step 2: Rewrite query with conversation context
            rewritten_query = await query_rewriter.rewrite_query(
                query=query,
                conversation_id=conversation_id,
                db=db
            )

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

            # Step 5: Get conversation history
            conversation_history = []
            if conversation:
                conversation_history = conversation.history if hasattr(conversation, 'history') else conversation.get('history', [])

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
                llm_response = await llm_service.generate_with_conversation(
                    prompt=prompt,
                    conversation_history=conversation_history,
                    stream=False
                )
            else:
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

            # Step 9: Format response for Slack
            formatted_response = response_formatter.format_answer_response(
                answer=processed_answer,
                citations=formatted_citations,
                confidence=None
            )

            # Step 10: Post response
            await slack_client_manager.post_message(
                channel=channel,
                text=formatted_response["text"],
                blocks=formatted_response["blocks"],
                thread_ts=thread_ts
            )

            logger.info(
                "ask_query_handled",
                user_id=user_id,
                channel=channel,
                conversation_id=conversation_id
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
            text = re.sub(r'<@[A-Z0-9]+>', '', text).strip()

            if not text:
                # If no text after removing mention, show help
                help_response = response_formatter.format_help_message()
                await slack_client_manager.post_message(
                    channel=channel,
                    text=help_response["text"],
                    blocks=help_response["blocks"],
                    thread_ts=thread_ts
                )
                return True

            # Check if it's a find or ask query based on keywords
            text_lower = text.lower()
            if any(keyword in text_lower for keyword in ["find", "search", "show me", "list"]):
                # Treat as find query
                return await self.handle_find_query(
                    query=text,
                    user_id=user_id,
                    channel=channel,
                    thread_ts=thread_ts,
                    db=db
                )
            else:
                # Default to ask query
                return await self.handle_ask_query(
                    query=text,
                    user_id=user_id,
                    channel=channel,
                    thread_ts=thread_ts,
                    db=db
                )

        except Exception as e:
            logger.error("handle_message_event_error", error=str(e))
            return False


# Global bot interaction service instance
bot_interaction_service = BotInteractionService()
