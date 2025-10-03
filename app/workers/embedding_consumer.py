"""Embedding generation consumer"""
import asyncio
from typing import Dict, Any
from app.workers.base_consumer import BaseConsumer
from app.services.embedding_service import embedding_service
from app.core.database import db_manager
from app.core.queue import QueueManager
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingConsumer(BaseConsumer):
    """Consumes embedding generation tasks"""

    def __init__(self):
        super().__init__(QueueManager.EMBEDDINGS_QUEUE)

    def process_message(self, message: Dict[str, Any]) -> bool:
        """Process embedding generation task"""

        task_type = message.get("type", "message")

        logger.info("embedding_task", type=task_type)

        try:
            # Use nest_asyncio to allow nested event loops
            import nest_asyncio
            nest_asyncio.apply()

            loop = asyncio.get_event_loop()

            if task_type == "file":
                return loop.run_until_complete(self._embed_file(message))
            else:
                # Default: message embedding
                return loop.run_until_complete(self._embed_message(message))

        except Exception as e:
            logger.error("embedding_task_error", type=task_type, error=str(e))
            return False

    async def _embed_message(self, message: Dict[str, Any]) -> bool:
        """Generate embedding for message"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                message_id = message.get("message_id")
                success = await embedding_service.embed_message(message_id, db)
                return success

            except Exception as e:
                logger.error("embed_message_failed", error=str(e))
                return False

    async def _embed_file(self, message: Dict[str, Any]) -> bool:
        """Generate embedding for file"""
        async with db_manager.AsyncSessionLocal() as db:
            try:
                file_id = message.get("file_id")
                success = await embedding_service.embed_file(file_id, db)
                return success

            except Exception as e:
                logger.error("embed_file_failed", error=str(e))
                return False


# Global embedding consumer instance
embedding_consumer = EmbeddingConsumer()


# Main entry point for running as standalone process
async def main():
    """Run embedding consumer as standalone process"""
    from app.core.cache import cache_manager
    from app.core.vector_db import vector_db_manager

    db_manager.initialize()
    await cache_manager.initialize()
    vector_db_manager.initialize()

    try:
        embedding_consumer.connect()
        embedding_consumer.start_consuming()
    finally:
        embedding_consumer.close()
        await db_manager.close()
        await cache_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
