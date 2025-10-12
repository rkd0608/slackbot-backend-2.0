"""Conversation history management service"""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import attributes
from app.models.conversation import Conversation
from app.core.logging import get_logger
import uuid

logger = get_logger(__name__)


class ConversationService:
    """Manages multi-turn conversation history"""

    MAX_TURNS_IN_MEMORY = 20  # Keep last 20 turns
    CONVERSATION_TIMEOUT_HOURS = 24  # Auto-close after 24 hours

    async def create_conversation(
        self,
        user_id: str,
        initial_query: str,
        db: AsyncSession
    ) -> str:
        """Create a new conversation"""

        conversation_id = str(uuid.uuid4())

        # Generate title from first query (first 50 chars)
        title = initial_query[:50] + "..." if len(initial_query) > 50 else initial_query

        conversation = Conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            turn_count=0,
            history=[],
            last_query=initial_query
        )

        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

        logger.info("conversation_created", conversation_id=conversation_id, user_id=user_id)

        return conversation_id

    async def add_turn(
        self,
        conversation_id: str,
        role: str,
        content: str,
        db: AsyncSession
    ) -> bool:
        """Add a turn to conversation history"""

        try:
            result = await db.execute(
                select(Conversation).where(Conversation.conversation_id == conversation_id)
            )
            conversation = result.scalar_one_or_none()

            if not conversation:
                logger.error("conversation_not_found", conversation_id=conversation_id)
                return False

            # Add turn to history
            turn = {
                "role": role,
                "content": content,
                "timestamp": datetime.utcnow().isoformat()
            }

            history = conversation.history or []
            logger.info(
                "before_add_turn",
                conversation_id=conversation_id,
                current_history_length=len(history),
                adding_role=role
            )
            history.append(turn)

            # Keep only last MAX_TURNS_IN_MEMORY turns
            if len(history) > self.MAX_TURNS_IN_MEMORY:
                history = history[-self.MAX_TURNS_IN_MEMORY:]

            # Update conversation
            conversation.history = history
            conversation.turn_count += 1
            conversation.last_interaction_at = datetime.utcnow()

            if role == "user":
                conversation.last_query = content
            elif role == "assistant":
                conversation.last_response = content

            # Mark the history field as modified to ensure SQLAlchemy commits the JSON change
            attributes.flag_modified(conversation, "history")

            await db.commit()
            await db.refresh(conversation)

            logger.info(
                "turn_added",
                conversation_id=conversation_id,
                role=role,
                turn_count=conversation.turn_count,
                final_history_length=len(conversation.history) if conversation.history else 0
            )

            return True

        except Exception as e:
            logger.error("add_turn_error", conversation_id=conversation_id, error=str(e))
            return False

    async def get_conversation(
        self,
        conversation_id: str,
        db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """Get conversation by ID"""

        result = await db.execute(
            select(Conversation).where(Conversation.conversation_id == conversation_id)
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            return None

        return {
            "conversation_id": conversation.conversation_id,
            "user_id": conversation.user_id,
            "title": conversation.title,
            "turn_count": conversation.turn_count,
            "history": conversation.history,
            "created_at": conversation.created_at.isoformat(),
            "last_interaction_at": conversation.last_interaction_at.isoformat()
        }

    async def get_user_conversations(
        self,
        user_id: str,
        db: AsyncSession,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get user's recent conversations"""

        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.last_interaction_at.desc())
            .limit(limit)
        )

        conversations = result.scalars().all()

        return [
            {
                "conversation_id": conv.conversation_id,
                "title": conv.title,
                "turn_count": conv.turn_count,
                "last_interaction_at": conv.last_interaction_at.isoformat(),
                "preview": conv.last_query[:100] if conv.last_query else ""
            }
            for conv in conversations
        ]

    async def get_conversation_history(
        self,
        conversation_id: str,
        db: AsyncSession
    ) -> List[Dict[str, str]]:
        """Get conversation history formatted for LLM"""

        result = await db.execute(
            select(Conversation).where(Conversation.conversation_id == conversation_id)
        )
        conversation = result.scalar_one_or_none()

        if not conversation or not conversation.history:
            return []

        # Format for LLM (role + content)
        formatted = []
        for turn in conversation.history:
            formatted.append({
                "role": turn["role"],
                "content": turn["content"]
            })

        return formatted

    async def cleanup_old_conversations(
        self,
        db: AsyncSession,
        hours: int = None
    ) -> int:
        """Clean up old inactive conversations"""

        timeout_hours = hours or self.CONVERSATION_TIMEOUT_HOURS
        cutoff_time = datetime.utcnow() - timedelta(hours=timeout_hours)

        result = await db.execute(
            select(Conversation).where(
                Conversation.last_interaction_at < cutoff_time
            )
        )

        old_conversations = result.scalars().all()

        for conv in old_conversations:
            await db.delete(conv)

        await db.commit()

        logger.info(
            "conversations_cleaned",
            count=len(old_conversations),
            cutoff_hours=timeout_hours
        )

        return len(old_conversations)

    async def delete_conversation(
        self,
        conversation_id: str,
        db: AsyncSession
    ) -> bool:
        """Delete a conversation"""

        try:
            result = await db.execute(
                select(Conversation).where(Conversation.conversation_id == conversation_id)
            )
            conversation = result.scalar_one_or_none()

            if conversation:
                await db.delete(conversation)
                await db.commit()

                logger.info("conversation_deleted", conversation_id=conversation_id)
                return True

            return False

        except Exception as e:
            logger.error("delete_conversation_error", conversation_id=conversation_id, error=str(e))
            return False

    async def get_or_create_by_thread(
        self,
        thread_ts: str,
        user_id: str,
        initial_query: str,
        db: AsyncSession
    ) -> str:
        """Get existing conversation for thread or create new one

        For now, we use conversation_id = thread_ts as a simple mapping.
        This ensures each Slack thread has exactly one conversation.
        """

        # Use thread_ts as conversation_id for direct mapping
        conversation_id = f"thread_{thread_ts}"

        # Check if conversation already exists
        result = await db.execute(
            select(Conversation).where(Conversation.conversation_id == conversation_id)
        )
        conversation = result.scalar_one_or_none()

        if conversation:
            logger.info(
                "conversation_found_for_thread",
                conversation_id=conversation_id,
                thread_ts=thread_ts
            )
            return conversation_id

        # Create new conversation for this thread
        title = initial_query[:50] + "..." if len(initial_query) > 50 else initial_query

        conversation = Conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            turn_count=0,
            history=[],
            last_query=initial_query
        )

        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

        logger.info(
            "conversation_created_for_thread",
            conversation_id=conversation_id,
            thread_ts=thread_ts,
            user_id=user_id
        )

        return conversation_id


# Global conversation service instance
conversation_service = ConversationService()
