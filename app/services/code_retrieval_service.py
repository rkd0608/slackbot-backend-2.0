"""Code retrieval service for specialized code search"""
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from app.models.code_snippet import CodeSnippet
from app.services.code_embedding_service import code_embedding_service
from app.core.vector_db import vector_db_manager
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CodeRetrievalService:
    """Specialized retrieval for code snippets using multi-strategy search"""

    async def retrieve_code(
        self,
        query: str,
        code_intent: Dict[str, Any],
        db: AsyncSession,
        top_k: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Multi-strategy code retrieval

        Strategies:
        1. Semantic search (embedding similarity in code namespace)
        2. Structural search (function/class name matching in DB)
        3. Hybrid (RRF fusion of both)

        Args:
            query: User's search query
            code_intent: Intent analysis from CodeQueryAnalyzer
            top_k: Number of results to return
            db: Database session

        Returns:
            List of code snippet results with metadata
        """
        if not settings.enable_code_intelligence:
            return []

        strategy = code_intent.get("search_strategy", "hybrid")

        logger.info(
            "code_retrieval_started",
            query=query[:100],
            strategy=strategy,
            language_hint=code_intent.get("language_hint")
        )

        try:
            if strategy == "structural":
                results = await self._structural_search(code_intent, db, top_k)
            elif strategy == "semantic":
                results = await self._semantic_code_search(query, code_intent, top_k)
            else:  # hybrid
                semantic_results = await self._semantic_code_search(query, code_intent, top_k * 2)
                structural_results = await self._structural_search(code_intent, db, top_k * 2)
                results = self._fuse_code_results(semantic_results, structural_results, top_k)

            # Hydrate results with full snippet data
            hydrated_results = await self._hydrate_results(results, db)

            logger.info(
                "code_retrieval_completed",
                query=query[:100],
                strategy=strategy,
                result_count=len(hydrated_results)
            )

            return hydrated_results

        except Exception as e:
            logger.error("code_retrieval_error", error=str(e), query=query[:100])
            return []

    async def _semantic_code_search(
        self,
        query: str,
        code_intent: Dict[str, Any],
        top_k: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Semantic search in code namespace using embeddings

        Flow:
        1. Generate query embedding using code embedding model
        2. Build metadata filter (language, type, etc.)
        3. Query Pinecone code namespace
        4. Return top-k results
        """
        try:
            # Step 1: Generate query embedding
            language_hint = code_intent.get("language_hint")
            query_embedding = await code_embedding_service.generate_query_embedding(
                query=query,
                language_hint=language_hint
            )

            # Step 2: Build metadata filter
            metadata_filter = {}

            if language_hint:
                metadata_filter["language"] = language_hint

            entity_type = code_intent.get("entity_type")
            if entity_type:
                metadata_filter["code_type"] = entity_type

            # Step 3: Initialize vector_db_manager if needed
            if vector_db_manager.index is None:
                vector_db_manager.initialize()

            # Step 4: Query Pinecone code namespace
            matches = vector_db_manager.query_namespace(
                namespace=settings.pinecone_code_namespace,
                vector=query_embedding,
                top_k=top_k,
                filter_dict=metadata_filter if metadata_filter else None,
                include_metadata=True
            )

            # Step 4: Format results
            results = []
            for match in matches:
                score = match.get("score", 0.0)

                logger.debug(
                    "code_match_score",
                    score=score,
                    min_score=settings.code_search_min_score,
                    snippet_id=match.get("metadata", {}).get("snippet_id")
                )

                # Filter by minimum score
                if score < settings.code_search_min_score:
                    continue

                metadata = match.get("metadata", {})

                results.append({
                    "snippet_id": metadata.get("snippet_id"),
                    "message_id": metadata.get("message_id"),
                    "score": score,
                    "source": "semantic_code",
                    "result_type": "code",
                    "language": metadata.get("language"),
                    "code_type": metadata.get("code_type"),
                    "function_name": metadata.get("function_name"),
                    "class_name": metadata.get("class_name"),
                    "metadata": metadata
                })

            logger.info(
                "semantic_code_search_completed",
                query=query[:100],
                top_k=top_k,
                results_count=len(results),
                filter=metadata_filter
            )

            return results

        except Exception as e:
            logger.error("semantic_code_search_error", error=str(e))
            return []

    async def _structural_search(
        self,
        code_intent: Dict[str, Any],
        db: AsyncSession,
        top_k: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Structural search using database queries on extracted metadata

        Searches for:
        - Function names
        - Class names
        - Import statements
        - Language-specific code
        """
        try:
            query_filters = []

            # Filter by language
            language_hint = code_intent.get("language_hint")
            if language_hint:
                query_filters.append(CodeSnippet.language == language_hint)

            # Filter by entity name
            entity_name = code_intent.get("entity_name")
            if entity_name:
                # Search in JSON metadata for function_name or class_name
                query_filters.append(
                    or_(
                        func.json_extract(
                            CodeSnippet.extracted_metadata,
                            "$.functions[*].name"
                        ).like(f"%{entity_name}%"),
                        func.json_extract(
                            CodeSnippet.extracted_metadata,
                            "$.classes[*].name"
                        ).like(f"%{entity_name}%")
                    )
                )

            # Filter by code type
            entity_type = code_intent.get("entity_type")
            if entity_type:
                query_filters.append(CodeSnippet.code_type == entity_type)

            # Execute query
            if not query_filters:
                # If no filters, return empty (avoid returning all code)
                return []

            result = await db.execute(
                select(CodeSnippet)
                .where(and_(*query_filters))
                .limit(top_k)
            )

            snippets = result.scalars().all()

            # Format results
            results = []
            for snippet in snippets:
                results.append({
                    "snippet_id": snippet.snippet_id,
                    "message_id": snippet.message_id,
                    "score": 1.0,  # Perfect match for structural search
                    "source": "structural_code",
                    "result_type": "code",
                    "language": snippet.language,
                    "code_type": snippet.code_type,
                    "metadata": snippet.extracted_metadata or {}
                })

            logger.info(
                "structural_code_search_completed",
                entity_name=entity_name,
                language=language_hint,
                results_count=len(results)
            )

            return results

        except Exception as e:
            logger.error("structural_code_search_error", error=str(e))
            return []

    def _fuse_code_results(
        self,
        semantic_results: List[Dict],
        structural_results: List[Dict],
        top_k: int
    ) -> List[Dict]:
        """
        Fuse semantic and structural results using RRF

        Reciprocal Rank Fusion formula:
        score(snippet) = sum(1 / (k + rank_i)) for each ranker i
        """
        k = 60  # RRF constant
        snippet_scores = {}

        # Process semantic results
        for idx, result in enumerate(semantic_results):
            snippet_id = result["snippet_id"]
            rrf_score = 1 / (k + idx + 1)

            if snippet_id not in snippet_scores:
                snippet_scores[snippet_id] = {
                    "rrf_score": 0.0,
                    "sources": [],
                    **result
                }

            snippet_scores[snippet_id]["rrf_score"] += rrf_score
            snippet_scores[snippet_id]["sources"].append("semantic_code")

        # Process structural results
        for idx, result in enumerate(structural_results):
            snippet_id = result["snippet_id"]
            rrf_score = 1 / (k + idx + 1)

            if snippet_id not in snippet_scores:
                snippet_scores[snippet_id] = {
                    "rrf_score": 0.0,
                    "sources": [],
                    **result
                }

            snippet_scores[snippet_id]["rrf_score"] += rrf_score
            snippet_scores[snippet_id]["sources"].append("structural_code")

        # Sort by RRF score and take top_k
        ranked = sorted(
            snippet_scores.values(),
            key=lambda x: x["rrf_score"],
            reverse=True
        )

        logger.info(
            "code_results_fused",
            semantic_count=len(semantic_results),
            structural_count=len(structural_results),
            fused_count=len(ranked),
            top_k=top_k
        )

        return ranked[:top_k]

    async def _hydrate_results(
        self,
        results: List[Dict],
        db: AsyncSession
    ) -> List[Dict]:
        """
        Hydrate results with full snippet data from database

        Adds:
        - raw_code
        - Full extracted_metadata
        - Message information
        """
        if not results:
            return []

        # Get all snippet IDs
        snippet_ids = [r["snippet_id"] for r in results]

        # Fetch snippets from database
        result = await db.execute(
            select(CodeSnippet).where(CodeSnippet.snippet_id.in_(snippet_ids))
        )
        snippets = result.scalars().all()

        # Create mapping
        snippet_map = {s.snippet_id: s for s in snippets}

        # Hydrate results
        hydrated = []
        for res in results:
            snippet_id = res["snippet_id"]
            snippet = snippet_map.get(snippet_id)

            if not snippet:
                continue

            hydrated_result = {
                **res,
                "raw_code": snippet.raw_code,
                "language": snippet.language,
                "code_type": snippet.code_type,
                "extracted_metadata": snippet.extracted_metadata or {},
                "snippet_index": snippet.snippet_index
            }

            hydrated.append(hydrated_result)

        return hydrated


# Global instance
code_retrieval_service = CodeRetrievalService()
