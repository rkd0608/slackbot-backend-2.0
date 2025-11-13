"""Pinecone vector database management"""
from typing import List, Dict, Optional, Any
from pinecone import Pinecone, ServerlessSpec
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class VectorDBManager:
    """Manages inPinecone vector database operations"""

    DIMENSION = 1536  # OpenAI text-embedding-3-large
    METRIC = "cosine"

    # Namespace naming convention for multi-tenancy
    NAMESPACE_MESSAGES = "messages"  # Slack messages
    NAMESPACE_CODE = "code"          # Code snippets
    NAMESPACE_FILES = "files"        # File embeddings

    # Legacy namespaces (for backward compatibility during migration)
    LEGACY_NAMESPACE_DEFAULT = ""   # Old default namespace
    LEGACY_NAMESPACE_CODE = "code"  # Old code namespace

    def __init__(self):
        self.pc: Optional[Pinecone] = None
        self.index = None

    @staticmethod
    def get_team_namespace(team_id: str, content_type: str) -> str:
        """
        Generate team-specific namespace

        Args:
            team_id: Slack team/workspace ID
            content_type: Type of content (messages, code, files)

        Returns:
            Namespace string in format: {team_id}:{content_type}

        Examples:
            >>> VectorDBManager.get_team_namespace("T0721T4PN4U", "messages")
            "T0721T4PN4U:messages"
            >>> VectorDBManager.get_team_namespace("T0721T4PN4U", "code")
            "T0721T4PN4U:code"
        """
        return f"{team_id}:{content_type}"

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

            logger.info(
                "pinecone_query_result_debug",
                filter=filter_dict,
                result_type=type(results).__name__,
                has_matches_attr=hasattr(results, 'matches'),
                matches_count=len(results.get("matches", [])) if hasattr(results, 'get') else (len(results.matches) if hasattr(results, 'matches') else 0)
            )

            return results.get("matches", [])
        except Exception as e:
            logger.error("vector_query_error", error=str(e), filter=filter_dict)
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

    def upsert_to_namespace(
        self,
        namespace: str,
        vectors: List[Dict[str, Any]]
    ) -> bool:
        """
        Upsert vectors to a specific Pinecone namespace

        Namespaces allow logical separation within the same index:
        - "default" or "" for regular message/file embeddings
        - "code" for code snippet embeddings
        - Future: "entities", "summaries", etc.

        Args:
            namespace: Namespace name (e.g., "code")
            vectors: List of vector dicts with id, values, metadata

        Returns:
            True if successful, False otherwise
        """
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

            # Upsert to specific namespace
            self.index.upsert(
                vectors=formatted_vectors,
                namespace=namespace
            )

            logger.info(
                "vectors_upserted_to_namespace",
                namespace=namespace,
                count=len(vectors)
            )
            return True

        except Exception as e:
            logger.error(
                "namespace_upsert_error",
                namespace=namespace,
                error=str(e)
            )
            return False

    def query_namespace(
        self,
        namespace: str,
        vector: List[float],
        top_k: int = 10,
        filter_dict: Optional[Dict[str, Any]] = None,
        include_metadata: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Query vectors from a specific Pinecone namespace

        Args:
            namespace: Namespace to query (e.g., "code")
            vector: Query vector
            top_k: Number of results to return
            filter_dict: Metadata filters
            include_metadata: Whether to include metadata

        Returns:
            List of matching vectors with metadata
        """
        try:
            results = self.index.query(
                namespace=namespace,
                vector=vector,
                top_k=top_k,
                filter=filter_dict,
                include_metadata=include_metadata
            )

            matches = results.get("matches", []) if hasattr(results, 'get') else results.matches

            logger.info(
                "namespace_query_completed",
                namespace=namespace,
                top_k=top_k,
                matches_count=len(matches),
                filter=filter_dict
            )

            return matches

        except Exception as e:
            logger.error(
                "namespace_query_error",
                namespace=namespace,
                error=str(e)
            )
            return []

    def delete_from_namespace(
        self,
        namespace: str,
        ids: List[str]
    ) -> bool:
        """Delete vectors from a specific namespace"""
        try:
            self.index.delete(
                ids=ids,
                namespace=namespace
            )

            logger.info(
                "vectors_deleted_from_namespace",
                namespace=namespace,
                count=len(ids)
            )
            return True

        except Exception as e:
            logger.error(
                "namespace_delete_error",
                namespace=namespace,
                error=str(e)
            )
            return False

    def delete_namespace(
        self,
        namespace: str
    ) -> bool:
        """
        Delete all vectors in a namespace

        This deletes ALL vectors in the specified namespace.
        Use with caution - this is irreversible!
        """
        try:
            # Delete all vectors in namespace using delete_all parameter
            self.index.delete(
                delete_all=True,
                namespace=namespace
            )

            logger.warning(
                "namespace_deleted",
                namespace=namespace,
                message="All vectors in namespace deleted"
            )
            return True

        except Exception as e:
            logger.error(
                "namespace_deletion_error",
                namespace=namespace,
                error=str(e)
            )
            return False

    def get_namespace_stats(self, namespace: str) -> Dict[str, Any]:
        """Get statistics for a specific namespace"""
        try:
            stats = self.index.describe_index_stats()

            # Extract namespace-specific stats
            namespaces = stats.get("namespaces", {})
            namespace_stats = namespaces.get(namespace, {})

            logger.info(
                "namespace_stats_retrieved",
                namespace=namespace,
                vector_count=namespace_stats.get("vector_count", 0)
            )

            return namespace_stats

        except Exception as e:
            logger.error(
                "namespace_stats_error",
                namespace=namespace,
                error=str(e)
            )
            return {}


# Global vector DB manager instance
vector_db_manager = VectorDBManager()
