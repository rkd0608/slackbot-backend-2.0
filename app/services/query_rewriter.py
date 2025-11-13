"""Query rewriting service for context resolution"""
from typing import Optional
from openai import AsyncOpenAI
from app.core.config import settings
from app.core.logging import get_logger
from app.services.conversation_service import conversation_service

logger = get_logger(__name__)


class QueryRewriter:
    """Rewrites queries with conversation context for better retrieval"""

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def rewrite_query(
        self,
        query: str,
        conversation_id: Optional[str] = None,
        db = None
    ) -> str:
        """
        Rewrite query using conversation history

        Args:
            query: The user's current query
            conversation_id: ID of current conversation
            db: Database session

        Returns:
            Standalone query that doesn't require context
        """
        # Check if feature is enabled
        if not settings.enable_query_rewriting:
            return query

        # If no conversation, return original query
        if not conversation_id or not db:
            return query

        # Get conversation history
        try:
            conversation = await conversation_service.get_conversation(
                conversation_id, db
            )

            if not conversation:
                return query

            # conversation is a dict, not a model object
            history = conversation.get('history', [])

            # If no history or very first turn, no rewriting needed
            if not history or len(history) < 2:
                return query

            # Get last N messages based on config
            max_history = settings.query_rewriter_max_history
            recent_history = history[-max_history:]

            # Build conversation context
            context_messages = []
            for turn in recent_history:
                role = turn.get('role', 'user')
                content = turn.get('content', '')
                context_messages.append(f"{role.capitalize()}: {content}")

            context_str = "\n".join(context_messages)

            # Call GPT to rewrite query
            response = await self.client.chat.completions.create(
                model=settings.query_rewriter_model,
                messages=[
                    {
                        "role": "system",
                        "content": """You are a query rewriter. Your job is to rewrite user queries into standalone questions that don't require conversation context.

Rules:
1. **DETECT TOPIC CHANGES**: If the new query introduces a completely different topic from the conversation history, return it UNCHANGED. Do NOT try to relate unrelated topics.
2. Resolve pronouns (it, that, this, they, the channel, etc.) to their actual referents
3. Include missing context from conversation history (especially channel names!)
4. Preserve the user's intent exactly
5. Keep the query concise
6. If the query is already standalone, return it unchanged
7. IMPORTANT: If the original query mentioned a specific channel (like #engineering), include it in ALL follow-up queries

Examples:
Input: "What about the database?"
History: "User: Tell me about the deployment\nAssistant: The deployment is scheduled for Monday"
Output: "What about the database related to the Monday deployment?"

Input: "When was that?"
History: "User: What did John say about Redis?\nAssistant: John mentioned Redis migration on March 5th"
Output: "When did John mention the Redis migration?"

Input: "Show me the code for it"
History: "User: How does authentication work?\nAssistant: We use JWT tokens..."
Output: "Show me the code for JWT authentication"

Input: "anything discussed on cache in the channel?"
History: "User: what was discussed in the #engineering channel?\nAssistant: Discussions in #engineering included..."
Output: "anything discussed on cache in the #engineering channel?"

Input: "any sql queries shared?"
History: "User: what was discussed in the #engineering channel?\nAssistant: Discussions in #engineering..."
Output: "any sql queries shared in the #engineering channel?"

Input: "ok I'm looking for a ai figma prompt"
History: "User: what was discussed about the event log issue?\nAssistant: The event log issue was addressed..."
Output: "ok I'm looking for a ai figma prompt"
(NOTE: "figma prompt" is a NEW TOPIC unrelated to "event logs" - return unchanged)

Input: "find me the slack bot code"
History: "User: what's the database schema?\nAssistant: Here's the database schema..."
Output: "find me the slack bot code"
(NOTE: "slack bot code" is UNRELATED to "database schema" - return unchanged)
"""
                    },
                    {
                        "role": "user",
                        "content": f"""Conversation history:
{context_str}

Current query: {query}

Rewrite this query as a standalone question. Return ONLY the rewritten query, nothing else."""
                    }
                ],
                temperature=0,
                max_tokens=150
            )

            rewritten = response.choices[0].message.content.strip()

            # Validation: If rewriting failed or returned empty, use original
            if not rewritten or len(rewritten) < 3:
                logger.warning(
                    "query_rewrite_empty",
                    original=query,
                    rewritten=rewritten
                )
                return query

            # Log the rewrite
            logger.info(
                "query_rewritten",
                original=query,
                rewritten=rewritten,
                context_turns=len(recent_history)
            )

            return rewritten

        except Exception as e:
            logger.error("query_rewrite_error", error=str(e), query=query)
            # On error, return original query
            return query


# Global instance
query_rewriter = QueryRewriter()
