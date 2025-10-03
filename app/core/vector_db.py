"""Pinecone vector database management"""
from typing import List, Dict, Optional, Any
from pinecone import Pinecone, ServerlessSpec
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class VectorDBManager:
    """Manages Pinecone vector database operations"""

    DIMENSION = 1536  # OpenAI text-embedding-3-large
    METRIC = "cosine"

    def __init__(self):
        self.pc: Optional[Pinecone] = None
        self.index = None

    def initialize(self):
        """Initialize Pinecone connection and index"""
        self.pc = Pinecone(api_key=settings.pinecone_api_key)

        # Create index if it doesn't exist
        existing_indexes = [idx.name for idx in self.pc.list_indexes()]

        if settings.pinecone_index_name not in existing_indexes:
            logger.info("creating_pinecone_index", index_name=settings.pinecone_index_name)
            self.pc.create_index(
                name=settings.pinecone_index_name,
                dimension=self.DIMENSION,
                metric=self.METRIC,
                spec=ServerlessSpec(
                    cloud="aws",
                    region=settings.pinecone_environment
                )
            )

        self.index = self.pc.Index(settings.pinecone_index_name)
        logger.info("vector_db_initialized", index_name=settings.pinecone_index_name)

    def upsert(self, vectors: List[Dict[str, Any]]) -> bool:
        """Upsert vectors to Pinecone"""
        try:
            # Format: [(id, values, metadata), ...]
            formatted_vectors = [
                (
                    vec["id"],
                    vec["values"],
                    vec.get("metadata", {})
                )
                for vec in vectors
            ]

            self.index.upsert(vectors=formatted_vectors)
            logger.info("vectors_upserted", count=len(vectors))
            return True
        except Exception as e:
            logger.error("vector_upsert_error", error=str(e))
            return False

    def query(
        self,
        vector: List[float],
        top_k: int = 10,
        filter_dict: Optional[Dict[str, Any]] = None,
        include_metadata: bool = True
    ) -> List[Dict[str, Any]]:
        """Query vectors from Pinecone"""
        try:
            results = self.index.query(
                vector=vector,
                top_k=top_k,
                filter=filter_dict,
                include_metadata=include_metadata
            )

            return results.get("matches", [])
        except Exception as e:
            logger.error("vector_query_error", error=str(e))
            return []

    def delete(self, ids: List[str]) -> bool:
        """Delete vectors by IDs"""
        try:
            self.index.delete(ids=ids)
            logger.info("vectors_deleted", count=len(ids))
            return True
        except Exception as e:
            logger.error("vector_delete_error", error=str(e))
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics"""
        try:
            stats = self.index.describe_index_stats()
            return stats
        except Exception as e:
            logger.error("vector_stats_error", error=str(e))
            return {}


# Global vector DB manager instance
vector_db_manager = VectorDBManager()
