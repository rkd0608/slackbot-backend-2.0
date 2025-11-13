"""
Conversational Context Service

Tracks conversation history to enable context-aware responses.

Key features:
- Maintains conversation sessions per user
- Extracts entities and topics from queries
- Provides context for query rewriting
- Automatically expires old contexts
- Enables natural follow-up questions

Example:
  User: "Tell me about authentication"
  Bot: [Response]
  User: "What about the API?"

  Context service knows:
  - Previous topic: authentication
  - Previous entities: [auth, login, oauth]
  - Can rewrite "What about the API?" -> "authentication API"
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, delete

from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.services.query_service import query_service

logger = get_logger(__name__)


class ConversationContextService:
    """
    Manages conversation context for natural follow-up questions

    Tracks:
    - Recent queries and their entities
    - Conversation flow (sequence of topics)
    - Active entities mentioned
    - User preferences and patterns
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.context_window_messages = 5  # Keep last 5 messages in context
        self.context_expiry_minutes = 30  # Context expires after 30 min

    async def get_or_create_conversation(
        self,
        user_id: str,
        team_id: str,
        channel_id: Optional[str] = None
    ) -> Conversation:
        """
        Get active conversation or create new one

        Args:
            user_id: Slack user ID
            team_id: Workspace team ID
            channel_id: Optional channel ID

        Returns:
            Conversation object
        """
        try:
            # Look for recent active conversation
            cutoff_time = datetime.utcnow() - timedelta(minutes=self.context_expiry_minutes)

            result = await self.db.execute(
                select(Conversation).where(
                    and_(
                        Conversation.user_id == user_id,
                        Conversation.team_id == team_id,
                        Conversation.updated_at >= cutoff_time
                    )
                ).order_by(desc(Conversation.updated_at)).limit(1)
            )
            conversation = result.scalar_one_or_none()

            if not conversation:
                # Create new conversation
                import uuid
                conversation = Conversation(
                    conversation_id=f"conv_{uuid.uuid4().hex[:16]}",
                    user_id=user_id,
                    team_id=team_id,
                    channel_id=channel_id
                )
                self.db.add(conversation)
                await self.db.commit()
                await self.db.refresh(conversation)

                logger.info(
                    "conversation_created",
                    conversation_id=conversation.id,
                    user_id=user_id
                )

            return conversation

        except Exception as e:
            logger.error("get_or_create_conversation_error", error=str(e))
            raise

    async def add_query_to_context(
        self,
        conversation_id: str,
        query: str,
        query_analysis: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Add a query to conversation context

        Args:
            conversation_id: Conversation ID
            query: User's query text
            query_analysis: Optional pre-computed analysis

        Returns:
            True if successful
        """
        try:
            # Get conversation
            result = await self.db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()

            if not conversation:
                logger.warning("conversation_not_found", conversation_id=conversation_id)
                return False

            # Analyze query if not provided
            if not query_analysis:
                query_analysis = await query_service.analyze_query(query)

            # Add to history
            history_entry = {
                'query': query,
                'timestamp': datetime.utcnow().isoformat(),
                'entities': query_analysis.get('entities', []),
                'intents': query_analysis.get('intents', []),
                'topics': query_analysis.get('topics', [])
            }

            # Update conversation history
            current_history = conversation.context.get('history', []) if conversation.context else []
            current_history.append(history_entry)

            # Keep only last N messages
            current_history = current_history[-self.context_window_messages:]

            # Update active entities (entities mentioned in recent queries)
            active_entities = set()
            for entry in current_history:
                active_entities.update([e['text'] for e in entry.get('entities', [])])

            # Update conversation
            conversation.context = {
                'history': current_history,
                'active_entities': list(active_entities),
                'last_topics': [entry.get('topics', []) for entry in current_history[-3:]],
                'last_intents': [entry.get('intents', []) for entry in current_history[-3:]]
            }
            conversation.message_count = len(current_history)
            conversation.updated_at = datetime.utcnow()

            await self.db.commit()

            logger.info(
                "query_added_to_context",
                conversation_id=conversation_id,
                entities_count=len(query_analysis.get('entities', [])),
                active_entities_count=len(active_entities)
            )

            return True

        except Exception as e:
            logger.error("add_query_to_context_error", error=str(e))
            return False

    async def get_context_for_query(
        self,
        conversation_id: str
    ) -> Dict[str, Any]:
        """
        Get conversation context for query rewriting

        Args:
            conversation_id: Conversation ID

        Returns:
            Dict with context information:
            - active_entities: List of entities mentioned recently
            - previous_topics: List of recent topics
            - previous_intents: List of recent intents
            - history: Recent query history
        """
        try:
            result = await self.db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()

            if not conversation or not conversation.context:
                return {
                    'active_entities': [],
                    'previous_topics': [],
                    'previous_intents': [],
                    'history': []
                }

            context = conversation.context

            return {
                'active_entities': context.get('active_entities', []),
                'previous_topics': context.get('last_topics', []),
                'previous_intents': context.get('last_intents', []),
                'history': context.get('history', [])[-3:]  # Last 3 queries
            }

        except Exception as e:
            logger.error("get_context_for_query_error", error=str(e))
            return {
                'active_entities': [],
                'previous_topics': [],
                'previous_intents': [],
                'history': []
            }

    async def rewrite_query_with_context(
        self,
        query: str,
        conversation_id: str
    ) -> Dict[str, Any]:
        """
        Rewrite query using conversation context

        Enhances vague queries with context from previous messages.

        Examples:
        - "What about the API?" -> "authentication API" (if previous topic was auth)
        - "Show me more" -> "Show me more discussions about database migration"
        - "Any issues?" -> "Any deployment issues?" (if discussing deployment)

        Args:
            query: User's current query
            conversation_id: Conversation ID

        Returns:
            Dict with:
            - original_query: Original query text
            - rewritten_query: Context-enhanced query
            - context_applied: Whether context was used
            - context_entities: Entities added from context
        """
        try:
            # Get context
            context = await self.get_context_for_query(conversation_id)

            # Analyze current query
            query_analysis = await query_service.analyze_query(query)

            # Check if query is vague (needs context)
            is_vague = self._is_vague_query(query, query_analysis)

            if not is_vague or not context['active_entities']:
                # Query is specific enough or no context available
                return {
                    'original_query': query,
                    'rewritten_query': query,
                    'context_applied': False,
                    'context_entities': []
                }

            # Rewrite with context
            rewritten = await self._apply_context_to_query(
                query=query,
                query_analysis=query_analysis,
                context=context
            )

            logger.info(
                "query_rewritten_with_context",
                original=query,
                rewritten=rewritten['rewritten_query'],
                context_entities=rewritten['context_entities']
            )

            return rewritten

        except Exception as e:
            logger.error("rewrite_query_with_context_error", error=str(e))
            return {
                'original_query': query,
                'rewritten_query': query,
                'context_applied': False,
                'context_entities': [],
                'error': str(e)
            }

    def _is_vague_query(
        self,
        query: str,
        query_analysis: Dict[str, Any]
    ) -> bool:
        """
        Determine if query is vague and needs context

        Vague queries:
        - "What about X?" (where X is generic like "API", "that")
        - "Show me more"
        - "Any issues?"
        - Short queries with few entities
        - Queries with pronouns (it, that, this)
        """
        query_lower = query.lower()

        # Vague patterns
        vague_patterns = [
            'what about',
            'show me more',
            'any ',
            'how about',
            'what else',
            'tell me about',
            ' it ',
            ' that ',
            ' this ',
            ' those ',
            ' these '
        ]

        has_vague_pattern = any(pattern in query_lower for pattern in vague_patterns)

        # Check entity count
        entity_count = len(query_analysis.get('entities', []))
        has_few_entities = entity_count <= 1

        # Check query length
        is_short = len(query.split()) <= 5

        return (has_vague_pattern or (has_few_entities and is_short))

    async def _apply_context_to_query(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply conversational context to enhance query

        Strategy:
        1. Identify what's missing (few entities, vague references)
        2. Add relevant entities from context
        3. Prefer recent entities over older ones
        4. Don't over-specify (max 2-3 context entities)
        """
        # Get most relevant context entities
        context_entities = self._select_relevant_context_entities(
            query=query,
            query_analysis=query_analysis,
            context=context
        )

        if not context_entities:
            return {
                'original_query': query,
                'rewritten_query': query,
                'context_applied': False,
                'context_entities': []
            }

        # Build rewritten query
        rewritten_query = self._build_rewritten_query(
            query=query,
            context_entities=context_entities
        )

        return {
            'original_query': query,
            'rewritten_query': rewritten_query,
            'context_applied': True,
            'context_entities': context_entities
        }

    def _select_relevant_context_entities(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        context: Dict[str, Any],
        max_entities: int = 2
    ) -> List[str]:
        """
        Select most relevant entities from context

        Scoring:
        - Recent mentions score higher
        - Entities from previous query score highest
        - Topic/concept entities preferred over generic terms
        """
        active_entities = context.get('active_entities', [])
        history = context.get('history', [])

        if not active_entities or not history:
            return []

        # Score entities
        entity_scores = {}

        for entity in active_entities:
            score = 0

            # Recency bonus (most recent = highest score)
            for i, entry in enumerate(reversed(history)):
                entry_entities = [e['text'] for e in entry.get('entities', [])]
                if entity.lower() in [e.lower() for e in entry_entities]:
                    score += (10 - i)  # Recent = higher score

            # Previous query bonus
            if history:
                last_entities = [e['text'] for e in history[-1].get('entities', [])]
                if entity.lower() in [e.lower() for e in last_entities]:
                    score += 20

            entity_scores[entity] = score

        # Sort by score and return top N
        sorted_entities = sorted(
            entity_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return [entity for entity, score in sorted_entities[:max_entities]]

    def _build_rewritten_query(
        self,
        query: str,
        context_entities: List[str]
    ) -> str:
        """
        Build rewritten query by adding context entities

        Examples:
        - "What about the API?" + ["authentication"]
          -> "What about the authentication API?"

        - "Show me more" + ["database", "migration"]
          -> "Show me more about database migration"

        - "Any issues?" + ["deployment"]
          -> "Any deployment issues?"
        """
        query_lower = query.lower()

        # Pattern: "what about X?"
        if 'what about' in query_lower:
            # Insert context before the subject
            parts = query.split('what about', 1)
            if len(parts) == 2:
                context_str = ' '.join(context_entities)
                return f"what about {context_str} {parts[1].strip()}"

        # Pattern: "show me more"
        if 'show me more' in query_lower:
            context_str = ' '.join(context_entities)
            return f"show me more about {context_str}"

        # Pattern: "any X?"
        if query_lower.startswith('any '):
            # Insert context before the noun
            parts = query.split(' ', 1)
            if len(parts) == 2:
                context_str = ' '.join(context_entities)
                return f"any {context_str} {parts[1]}"

        # Default: prepend context entities
        context_str = ' '.join(context_entities)
        return f"{context_str} {query}"

    async def cleanup_expired_conversations(
        self,
        team_id: str,
        days_old: int = 7
    ) -> int:
        """
        Clean up expired conversations

        Args:
            team_id: Workspace team ID
            days_old: Delete conversations older than this

        Returns:
            Number of conversations deleted
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_old)

            result = await self.db.execute(
                delete(Conversation).where(
                    and_(
                        Conversation.team_id == team_id,
                        Conversation.updated_at < cutoff_date
                    )
                )
            )

            await self.db.commit()

            deleted_count = result.rowcount

            logger.info(
                "conversations_cleaned_up",
                team_id=team_id,
                deleted_count=deleted_count
            )

            return deleted_count

        except Exception as e:
            logger.error("cleanup_expired_conversations_error", error=str(e))
            return 0


def get_conversation_context_service(db: AsyncSession) -> ConversationContextService:
    """Get conversation context service instance"""
    return ConversationContextService(db)
