"""Embedding generation service using OpenAI"""
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.message import Message
from app.models.thread import Thread
from app.models.channel import Channel
from app.core.config import settings
from app.core.vector_db import vector_db_manager
from app.core.logging import get_logger
from app.core.monitoring import embedding_generation_rate
import uuid

logger = get_logger(__name__)


class EmbeddingService:
    """Generates embeddings using OpenAI API"""

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_embedding_model
        self.dimension = 1536  # text-embedding-3-large

    async def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for single text"""
        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimension
            )

            embedding = response.data[0].embedding
            embedding_generation_rate.inc()

            return embedding

        except Exception as e:
            logger.error("embedding_generation_error", error=str(e))
            return None

    async def generate_embeddings_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Generate embeddings for batch of texts"""
        try:
            # OpenAI supports up to 100 inputs per request
            batch_size = min(len(texts), settings.embedding_batch_size)

            response = await self.client.embeddings.create(
                model=self.model,
                input=texts[:batch_size],
                dimensions=self.dimension
            )

            embeddings = [item.embedding for item in response.data]
            embedding_generation_rate.inc(len(embeddings))

            return embeddings

        except Exception as e:
            logger.error("batch_embedding_error", error=str(e), count=len(texts))
            return [None] * len(texts)

    async def embed_message(self, message_id: str, db: AsyncSession) -> bool:
        """Generate and store embedding for a message with context"""
        try:
            # Fetch message
            result = await db.execute(
                select(Message).where(Message.message_id == message_id)
            )
            message = result.scalar_one_or_none()

            if not message:
                logger.error("message_not_found", message_id=message_id)
                return False

            # Skip if already embedded (prevent duplicates)
            if message.vector_id:
                logger.info("message_already_embedded", message_id=message_id, vector_id=message.vector_id)
                return True

            # Build context for embedding
            context_text = await self._build_message_context(message, db)

            # Generate embedding
            embedding = await self.generate_embedding(context_text)

            if not embedding:
                return False

            # Create vector ID
            vector_id = str(uuid.uuid4())

            # Prepare metadata for Pinecone
            metadata = {
                "message_id": message.message_id,
                "channel_id": message.channel_id,
                "channel_name": message.channel_name or "",
                "user_id": message.user_id,
                "user_name": message.user_name or "",
                "timestamp": message.timestamp.timestamp(),  # Unix timestamp for numeric filtering
                "thread_ts": message.thread_ts or "",
                "is_thread_parent": message.is_thread_parent,
                "text": message.text[:1000] if message.text else "",  # Truncate for metadata
                "has_code": message.has_code,
                "code_languages": message.code_languages or [],
                "mentioned_users": message.mentioned_users or [],
                "reaction_count": message.reaction_count,
                "reply_count": message.reply_count,
                "importance_score": message.importance_score,
                "has_files": message.has_files
            }

            # Upsert to Pinecone
            success = vector_db_manager.upsert([{
                "id": vector_id,
                "values": embedding,
                "metadata": metadata
            }])

            if success:
                # Update message with vector_id
                message.vector_id = vector_id
                await db.commit()

                logger.info("message_embedded", message_id=message_id, vector_id=vector_id)
                return True

            return False

        except Exception as e:
            logger.error("embed_message_error", message_id=message_id, error=str(e))
            return False

    async def _build_message_context(self, message: Message, db: AsyncSession) -> str:
        """Build contextual text for embedding generation"""

        # Start with channel context
        context_parts = [
            f"Channel: {message.channel_name or message.channel_id}"
        ]

        # Add thread context if applicable
        if message.thread_ts:
            # Get thread parent if this is a reply
            if message.thread_ts != message.message_id:
                result = await db.execute(
                    select(Message).where(Message.message_id == message.thread_ts)
                )
                parent = result.scalar_one_or_none()

                if parent and parent.text:
                    context_parts.append(f"Thread Topic: {parent.text[:200]}")

            # Get thread summary if available
            result = await db.execute(
                select(Thread).where(Thread.thread_ts == message.thread_ts)
            )
            thread = result.scalar_one_or_none()

            if thread and thread.summary:
                context_parts.append(f"Thread Summary: {thread.summary[:200]}")

        # Add the actual message
        context_parts.append(f"Message: {message.text_processed or message.text}")

        # Add code context if present
        if message.has_code and message.code_languages:
            context_parts.append(f"Contains code: {', '.join(message.code_languages)}")

        return "\n".join(context_parts)

    async def embed_file(self, file_id: str, db: AsyncSession) -> bool:
        """Generate and store embedding for a file"""
        try:
            from app.models.file import File
            from app.models.channel import Channel

            result = await db.execute(
                select(File).where(File.file_id == file_id)
            )
            file_record = result.scalar_one_or_none()

            if not file_record or not file_record.extracted_text:
                return False

            # Skip if already embedded (prevent duplicates)
            if file_record.vector_id:
                logger.info("file_already_embedded", file_id=file_id, vector_id=file_record.vector_id)
                return True

            # Get channel name and team_id
            channel_name = None
            team_id = None
            if file_record.channel_id:
                channel_result = await db.execute(
                    select(Channel).where(Channel.channel_id == file_record.channel_id)
                )
                channel = channel_result.scalar_one_or_none()
                if channel:
                    channel_name = channel.channel_name
                    team_id = channel.team_id

            # Build context
            context = f"File: {file_record.filename}\n"
            if file_record.title:
                context += f"Title: {file_record.title}\n"
            if channel_name:
                context += f"Channel: #{channel_name}\n"
            context += f"Content: {file_record.extracted_text}"

            # Generate embedding
            embedding = await self.generate_embedding(context)

            if not embedding:
                return False

            # Create vector ID
            vector_id = str(uuid.uuid4())

            # Metadata
            metadata = {
                "type": "file",
                "file_id": file_record.file_id,
                "channel_id": file_record.channel_id,
                "user_id": file_record.user_id,
                "filename": file_record.filename,
                "filetype": file_record.filetype or "",
                "timestamp": file_record.created_at.isoformat(),
                "text": file_record.extracted_text[:1000]
            }

            # Add team_id if available
            if team_id:
                metadata["team_id"] = team_id

            # Add channel_name if available
            if channel_name:
                metadata["channel_name"] = channel_name

            # Upsert to Pinecone
            success = vector_db_manager.upsert([{
                "id": vector_id,
                "values": embedding,
                "metadata": metadata
            }])

            if success:
                file_record.vector_id = vector_id
                await db.commit()

                logger.info("file_embedded", file_id=file_id, vector_id=vector_id)
                return True

            return False

        except Exception as e:
            logger.error("embed_file_error", file_id=file_id, error=str(e))
            return False


# Global embedding service instance
embedding_service = EmbeddingService()
