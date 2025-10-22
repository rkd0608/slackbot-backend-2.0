"""Code embedding service with multi-provider support (Voyage AI, OpenAI)"""
from typing import List, Optional, Dict, Any
from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models.code_snippet import CodeSnippet
from app.core.config import settings
from app.core.logging import get_logger
from app.core.vector_db import vector_db_manager

logger = get_logger(__name__)


class CodeEmbeddingProvider(ABC):
    """Abstract interface for code embedding providers"""

    @abstractmethod
    async def embed_code(
        self,
        code: str,
        language: str,
        metadata: Optional[Dict] = None
    ) -> List[float]:
        """Generate embedding for code snippet"""
        pass


class VoyageCodeEmbedding(CodeEmbeddingProvider):
    """Voyage AI Code-2 embeddings (RECOMMENDED for code)"""

    def __init__(self):
        if not settings.voyage_api_key:
            logger.warning("voyage_api_key_not_configured")
            raise ValueError("VOYAGE_API_KEY not configured")

        try:
            import voyageai
            self.client = voyageai.Client(api_key=settings.voyage_api_key)
            logger.info("voyage_client_initialized")
        except ImportError:
            logger.error("voyageai_package_not_installed")
            raise ImportError("voyageai package not installed. Run: pip install voyageai")

    async def embed_code(
        self,
        code: str,
        language: str,
        metadata: Optional[Dict] = None
    ) -> List[float]:
        """
        Generate Voyage Code-2 embeddings

        Benefits:
        - Optimized specifically for code semantic search
        - 1536 dimensions (same as OpenAI)
        - Better code understanding than general text models
        - $0.12 per 1M tokens (~$0.003 per code block)
        """
        try:
            # Voyage AI Code-2 model
            response = self.client.embed(
                texts=[code],
                model=settings.code_embedding_model,
                input_type="document"  # Use "document" for indexing
            )

            embedding = response.embeddings[0]

            logger.debug(
                "voyage_embedding_generated",
                language=language,
                code_length=len(code),
                embedding_dim=len(embedding)
            )

            return embedding

        except Exception as e:
            logger.error("voyage_embedding_error", error=str(e), language=language)
            raise


class OpenAICodeEmbedding(CodeEmbeddingProvider):
    """OpenAI text-embedding-3-large (fallback for code)"""

    def __init__(self):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        logger.info("openai_code_embedding_initialized")

    async def embed_code(
        self,
        code: str,
        language: str,
        metadata: Optional[Dict] = None
    ) -> List[float]:
        """
        Generate OpenAI embeddings with code-specific enhancements

        Strategy:
        - Prepend language hint to improve semantic understanding
        - Use same dimension as text embeddings (1536) for consistency
        - Not specialized for code but decent fallback
        """
        try:
            # Enhance code with language hint for better embeddings
            enhanced_text = f"# Language: {language}\n{code}"

            response = await self.client.embeddings.create(
                model="text-embedding-3-large",
                input=enhanced_text,
                dimensions=1536  # Match text embeddings dimension
            )

            embedding = response.data[0].embedding

            logger.debug(
                "openai_code_embedding_generated",
                language=language,
                code_length=len(code),
                embedding_dim=len(embedding)
            )

            return embedding

        except Exception as e:
            logger.error("openai_code_embedding_error", error=str(e))
            raise


class CodeEmbeddingService:
    """Main service for code embeddings with multi-provider support"""

    def __init__(self):
        # Initialize provider based on config
        self.provider = self._initialize_provider()

    def _initialize_provider(self) -> CodeEmbeddingProvider:
        """Initialize embedding provider based on configuration"""
        provider_name = settings.code_embedding_provider.lower()

        if provider_name == "voyage":
            try:
                return VoyageCodeEmbedding()
            except Exception as e:
                logger.warning(
                    "voyage_init_failed_fallback_to_openai",
                    error=str(e)
                )
                return OpenAICodeEmbedding()
        elif provider_name == "openai":
            return OpenAICodeEmbedding()
        else:
            logger.warning(
                "unknown_provider_fallback_to_openai",
                provider=provider_name
            )
            return OpenAICodeEmbedding()

    async def embed_code_snippet(
        self,
        snippet_id: str,
        db: AsyncSession
    ) -> bool:
        """
        Generate embedding for a code snippet and upsert to Pinecone

        Flow:
        1. Fetch snippet from database
        2. Generate embedding using provider
        3. Prepare metadata for Pinecone
        4. Upsert to Pinecone CODE namespace
        5. Update snippet.vector_id in database

        Returns:
            True if successful, False otherwise
        """
        if not settings.enable_code_intelligence:
            logger.debug("code_intelligence_disabled")
            return False

        try:
            # Step 1: Fetch snippet
            result = await db.execute(
                select(CodeSnippet).where(CodeSnippet.snippet_id == snippet_id)
            )
            snippet = result.scalar_one_or_none()

            if not snippet:
                logger.error("snippet_not_found", snippet_id=snippet_id)
                return False

            # Skip if already embedded
            if snippet.vector_id:
                logger.info(
                    "snippet_already_embedded",
                    snippet_id=snippet_id,
                    vector_id=snippet.vector_id
                )
                return True

            # Step 2: Generate embedding
            embedding = await self.provider.embed_code(
                code=snippet.raw_code,
                language=snippet.language,
                metadata=snippet.extracted_metadata
            )

            # Step 3: Prepare metadata for Pinecone
            pinecone_metadata = self._prepare_pinecone_metadata(snippet)

            # Step 4: Initialize vector_db_manager if needed
            if vector_db_manager.index is None:
                vector_db_manager.initialize()

            # Step 5: Get team_id from message for namespace
            from app.models.message import Message
            message_result = await db.execute(
                select(Message).where(Message.message_id == snippet.message_id)
            )
            message = message_result.scalar_one_or_none()

            if not message or not message.team_id:
                logger.error("message_or_team_id_not_found", snippet_id=snippet_id, message_id=snippet.message_id)
                return False

            # Add team_id to metadata for filtering
            pinecone_metadata["team_id"] = message.team_id

            # Step 6: Upsert to team-specific CODE namespace for multi-tenancy
            namespace = vector_db_manager.get_team_namespace(
                message.team_id,
                vector_db_manager.NAMESPACE_CODE
            )
            success = vector_db_manager.upsert_to_namespace(
                namespace=namespace,
                vectors=[{
                    "id": snippet_id,
                    "values": embedding,
                    "metadata": pinecone_metadata
                }]
            )

            if not success:
                logger.error("pinecone_upsert_failed", snippet_id=snippet_id)
                return False

            # Step 7: Update snippet with vector_id
            await db.execute(
                update(CodeSnippet)
                .where(CodeSnippet.snippet_id == snippet_id)
                .values(vector_id=snippet_id)
            )
            await db.commit()

            logger.info(
                "code_snippet_embedded",
                snippet_id=snippet_id,
                language=snippet.language,
                code_type=snippet.code_type,
                namespace=namespace,
                team_id=message.team_id
            )

            return True

        except Exception as e:
            logger.error(
                "embed_code_snippet_error",
                snippet_id=snippet_id,
                error=str(e)
            )
            await db.rollback()
            return False

    def _prepare_pinecone_metadata(self, snippet: CodeSnippet) -> Dict[str, Any]:
        """
        Prepare metadata for Pinecone storage

        Metadata includes:
        - snippet_id, message_id, language, code_type
        - Extracted entities (function names, class names)
        - Code characteristics (has_docstring, complexity)
        - Channel/team for filtering
        """
        metadata = snippet.extracted_metadata or {}

        # Extract key entities
        functions = metadata.get("functions", [])
        classes = metadata.get("classes", [])

        function_names = []
        if functions:
            function_names = [f.get("name") for f in functions if isinstance(f, dict) and f.get("name")]

        class_names = []
        if classes:
            class_names = [c.get("name") for c in classes if isinstance(c, dict) and c.get("name")]

        # Build Pinecone metadata (must be JSON-serializable)
        pinecone_meta = {
            "snippet_id": snippet.snippet_id,
            "message_id": snippet.message_id,
            "language": snippet.language or "unknown",
            "code_type": snippet.code_type or "snippet",

            # Entity information
            "function_name": function_names[0] if function_names else None,
            "class_name": class_names[0] if class_names else None,
            "function_count": len(function_names),
            "class_count": len(class_names),

            # Code characteristics
            "has_docstring": metadata.get("has_docstring", False),
            "line_count": metadata.get("line_count", 0),
            "complexity_score": float(metadata.get("complexity_score", 0)) if metadata.get("complexity_score") else 0,
            "is_inline": metadata.get("is_inline", False),

            # Imports (for dependency search)
            "import_count": len(metadata.get("imports", [])),

            # Type (for filtering)
            "type": "code"  # Mark as code for filtering
        }

        # Remove None values to save space
        return {k: v for k, v in pinecone_meta.items() if v is not None}

    async def batch_embed_snippets(
        self,
        snippet_ids: List[str],
        db: AsyncSession
    ) -> Dict[str, int]:
        """
        Batch embed multiple code snippets

        Returns:
            Dict with success/failure counts
        """
        results = {
            "success": 0,
            "failed": 0,
            "skipped": 0
        }

        for snippet_id in snippet_ids:
            success = await self.embed_code_snippet(snippet_id, db)

            if success:
                results["success"] += 1
            else:
                results["failed"] += 1

        logger.info(
            "batch_embedding_completed",
            total=len(snippet_ids),
            **results
        )

        return results

    async def generate_query_embedding(
        self,
        query: str,
        language_hint: Optional[str] = None
    ) -> List[float]:
        """
        Generate embedding for a code search query

        Used during retrieval to find similar code
        """
        try:
            # Use query as-is or enhance with language hint
            search_text = query
            if language_hint:
                search_text = f"# Language: {language_hint}\n{query}"

            embedding = await self.provider.embed_code(
                code=search_text,
                language=language_hint or "unknown",
                metadata={"is_query": True}
            )

            return embedding

        except Exception as e:
            logger.error("query_embedding_error", error=str(e))
            raise


# Global instance
code_embedding_service = CodeEmbeddingService()
