"""Multi-stage retrieval service"""
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from app.models.message import Message
from app.models.thread import Thread
from app.models.channel import Channel
from app.services.embedding_service import embedding_service
from app.core.vector_db import vector_db_manager
from app.core.config import settings
from app.core.logging import get_logger
from app.core.monitoring import query_latency
import time

logger = get_logger(__name__)


class RetrievalService:
    """Multi-stage retrieval pipeline"""

    def __init__(self):
        self.retrieval_candidates = settings.retrieval_candidates
        self.rerank_top_k = settings.rerank_top_k

    async def retrieve(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        user_id: str,
        db: AsyncSession,
        top_k: int = 50
    ) -> List[Dict[str, Any]]:
        """Main retrieval pipeline with multiple strategies"""

        logger.info("retrieval_started", query=query[:100])

        # Stage 1: Parallel candidate generation
        start_time = time.time()

        # Run all retrieval strategies in parallel
        semantic_results, keyword_results, entity_results = await self._parallel_retrieval(
            query,
            query_analysis,
            user_id,
            db
        )

        retrieval_time = time.time() - start_time
        query_latency.labels(stage="retrieval").observe(retrieval_time)

        logger.info(
            "candidates_generated",
            semantic=len(semantic_results),
            keyword=len(keyword_results),
            entity=len(entity_results),
            time_ms=int(retrieval_time * 1000)
        )

        # Stage 2: Fusion and deduplication
        start_time = time.time()

        fused_results = self._reciprocal_rank_fusion(
            semantic_results,
            keyword_results,
            entity_results
        )

        fusion_time = time.time() - start_time
        query_latency.labels(stage="fusion").observe(fusion_time)

        # Take top candidates for reranking
        candidates = fused_results[:self.retrieval_candidates]

        logger.info(
            "fusion_completed",
            candidates=len(candidates),
            time_ms=int(fusion_time * 1000)
        )

        # Stage 3: Reranking (if needed)
        if len(candidates) > top_k:
            start_time = time.time()

            reranked = await self._rerank_results(query, candidates, db)

            rerank_time = time.time() - start_time
            query_latency.labels(stage="reranking").observe(rerank_time)

            final_results = reranked[:top_k]

            logger.info(
                "reranking_completed",
                results=len(final_results),
                time_ms=int(rerank_time * 1000)
            )
        else:
            final_results = candidates[:top_k]

        # Stage 4: Hydrate results with human-readable names
        hydrated_results = await self._hydrate_results(final_results, db)

        return hydrated_results

    async def _parallel_retrieval(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        user_id: str,
        db: AsyncSession
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run multiple retrieval strategies in parallel"""

        import asyncio

        # Execute all strategies concurrently
        semantic_task = self._semantic_search(query, query_analysis, user_id, db)
        keyword_task = self._keyword_search(query, query_analysis, user_id, db)
        entity_task = self._entity_based_search(query, query_analysis, user_id, db)

        semantic_results, keyword_results, entity_results = await asyncio.gather(
            semantic_task,
            keyword_task,
            entity_task,
            return_exceptions=True
        )

        # Handle exceptions
        if isinstance(semantic_results, Exception):
            logger.error("semantic_search_error", error=str(semantic_results))
            semantic_results = []

        if isinstance(keyword_results, Exception):
            logger.error("keyword_search_error", error=str(keyword_results))
            keyword_results = []

        if isinstance(entity_results, Exception):
            logger.error("entity_search_error", error=str(entity_results))
            entity_results = []

        return semantic_results, keyword_results, entity_results

    async def _semantic_search(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        user_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """Semantic vector search using Pinecone"""

        try:
            # Rewrite query to improve semantic matching
            search_query = self._rewrite_query(query, query_analysis)
            logger.info("query_rewritten", original=query[:100], rewritten=search_query[:100])

            # Expand query for better recall (if enabled)
            from app.services.query_expander import query_expander
            query_variations = await query_expander.expand_query(search_query, max_expansions=2)

            # Build metadata filter (same for all variations)
            metadata_filter = await self._build_metadata_filter(
                query_analysis,
                user_id,
                db
            )

            # Search with each query variation
            all_results = []
            seen_message_ids = {}  # Track best score for each message

            for q_variant in query_variations:
                # Generate embedding for this variation
                query_embedding = await embedding_service.generate_embedding(q_variant)

                if not query_embedding:
                    continue

                # Query Pinecone
                matches = vector_db_manager.query(
                    vector=query_embedding,
                    top_k=50,  # Get fewer per variation
                    filter_dict=metadata_filter,
                    include_metadata=True
                )

                # Collect results, tracking best score per message
                for match in matches:
                    msg_id = match.get("metadata", {}).get("message_id")
                    score = match.get("score", 0.0)

                    # Keep highest score for each message
                    if msg_id not in seen_message_ids or score > seen_message_ids[msg_id]["score"]:
                        seen_message_ids[msg_id] = {
                            "message_id": msg_id,
                            "score": score,
                            "source": "semantic",
                            "query_variant": q_variant if len(query_variations) > 1 else None,
                            "metadata": match.get("metadata", {})
                        }

            # Convert to list and sort by score
            results = list(seen_message_ids.values())
            results.sort(key=lambda x: x["score"], reverse=True)
            results = results[:100]  # Top 100

            logger.info(
                "semantic_search_completed",
                query_variations=len(query_variations),
                unique_results=len(results)
            )
            return results

        except Exception as e:
            logger.error("semantic_search_error", error=str(e))
            return []

    async def _keyword_search(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        user_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """Keyword-based search using database"""

        try:
            # Build SQL query
            query_filters = [Message.text.ilike(f"%{query}%")]

            # Add channel filter
            if query_analysis.get("channels"):
                channel_names = query_analysis["channels"]
                query_filters.append(Message.channel_name.in_(channel_names))

            # Add user filter
            if query_analysis.get("users"):
                user_mentions = query_analysis["users"]
                # Check both user_name and mentioned_users
                query_filters.append(
                    or_(
                        Message.user_name.in_(user_mentions),
                        Message.mentioned_users.contains(user_mentions)
                    )
                )

            # Add temporal filter
            temporal = query_analysis.get("temporal")
            if temporal and temporal.get("start_date"):
                query_filters.append(Message.timestamp >= temporal["start_date"])
            if temporal and temporal.get("end_date"):
                query_filters.append(Message.timestamp <= temporal["end_date"])

            # Code filter
            if query_analysis.get("has_code_intent"):
                query_filters.append(Message.has_code == True)

            # Execute query
            result = await db.execute(
                select(Message)
                .where(and_(*query_filters))
                .order_by(Message.importance_score.desc())
                .limit(50)
            )

            messages = result.scalars().all()

            # Format results with BM25-like scoring
            results = []
            for idx, msg in enumerate(messages):
                # Simple scoring based on position and importance
                score = (50 - idx) / 50.0 * 0.5 + msg.importance_score / 10.0 * 0.5

                results.append({
                    "message_id": msg.message_id,
                    "score": score,
                    "source": "keyword",
                    "metadata": {
                        "channel_id": msg.channel_id,
                        "channel_name": msg.channel_name,
                        "user_id": msg.user_id,
                        "user_name": msg.user_name,
                        "timestamp": msg.timestamp.isoformat(),
                        "text": msg.text[:500] if msg.text else "",
                        "importance_score": msg.importance_score
                    }
                })

            logger.info("keyword_search_completed", results=len(results))
            return results

        except Exception as e:
            logger.error("keyword_search_error", error=str(e))
            return []

    async def _entity_based_search(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        user_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """Entity-based retrieval using extracted entities"""

        try:
            entities = query_analysis.get("entities", [])

            if not entities:
                return []

            # Extract entity texts
            entity_texts = [e["text"] for e in entities]

            # Find messages containing these entities
            query_filters = []
            for entity_text in entity_texts:
                query_filters.append(Message.text.ilike(f"%{entity_text}%"))

            if not query_filters:
                return []

            result = await db.execute(
                select(Message)
                .where(or_(*query_filters))
                .order_by(Message.importance_score.desc())
                .limit(30)
            )

            messages = result.scalars().all()

            # Score based on entity matches
            results = []
            for msg in messages:
                # Count how many entities match
                match_count = sum(
                    1 for entity in entity_texts
                    if entity.lower() in (msg.text or "").lower()
                )

                score = match_count / len(entity_texts) * 0.7 + msg.importance_score / 10.0 * 0.3

                results.append({
                    "message_id": msg.message_id,
                    "score": score,
                    "source": "entity",
                    "metadata": {
                        "channel_id": msg.channel_id,
                        "channel_name": msg.channel_name,
                        "user_id": msg.user_id,
                        "user_name": msg.user_name,
                        "timestamp": msg.timestamp.isoformat(),
                        "text": msg.text[:500] if msg.text else "",
                        "importance_score": msg.importance_score,
                        "matched_entities": match_count
                    }
                })

            logger.info("entity_search_completed", results=len(results))
            return results

        except Exception as e:
            logger.error("entity_search_error", error=str(e))
            return []

    def _reciprocal_rank_fusion(
        self,
        semantic_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        entity_results: List[Dict[str, Any]],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """Combine multiple ranking lists using Reciprocal Rank Fusion"""

        # Aggregate scores by message_id
        scores = {}

        # Process semantic results
        for idx, result in enumerate(semantic_results):
            msg_id = result["message_id"]
            rrf_score = 1 / (k + idx + 1)

            if msg_id not in scores:
                scores[msg_id] = {
                    "message_id": msg_id,
                    "rrf_score": 0.0,
                    "sources": [],
                    "metadata": result.get("metadata", {})
                }

            scores[msg_id]["rrf_score"] += rrf_score
            scores[msg_id]["sources"].append("semantic")

        # Process keyword results
        for idx, result in enumerate(keyword_results):
            msg_id = result["message_id"]
            rrf_score = 1 / (k + idx + 1)

            if msg_id not in scores:
                scores[msg_id] = {
                    "message_id": msg_id,
                    "rrf_score": 0.0,
                    "sources": [],
                    "metadata": result.get("metadata", {})
                }

            scores[msg_id]["rrf_score"] += rrf_score
            scores[msg_id]["sources"].append("keyword")

        # Process entity results
        for idx, result in enumerate(entity_results):
            msg_id = result["message_id"]
            rrf_score = 1 / (k + idx + 1)

            if msg_id not in scores:
                scores[msg_id] = {
                    "message_id": msg_id,
                    "rrf_score": 0.0,
                    "sources": [],
                    "metadata": result.get("metadata", {})
                }

            scores[msg_id]["rrf_score"] += rrf_score
            scores[msg_id]["sources"].append("entity")

        # Sort by RRF score
        ranked = sorted(
            scores.values(),
            key=lambda x: x["rrf_score"],
            reverse=True
        )

        return ranked

    async def _rerank_results(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """Rerank candidates using cross-encoder or feature-based scoring"""

        # For now, use feature-based reranking
        # In production, you'd use a cross-encoder model like ms-marco-MiniLM

        reranked = []

        for candidate in candidates:
            metadata = candidate.get("metadata", {})

            # Feature-based scoring
            features = {
                "rrf_score": candidate.get("rrf_score", 0.0),
                "importance": metadata.get("importance_score", 0.0) / 10.0,
                "recency": self._recency_score(metadata.get("timestamp")),
                "reactions": min(metadata.get("reaction_count", 0) / 10.0, 1.0),
                "has_code": 1.0 if metadata.get("has_code") else 0.0,
                "source_diversity": len(candidate.get("sources", [])) / 3.0
            }

            # Weighted combination
            final_score = (
                features["rrf_score"] * 0.4 +
                features["importance"] * 0.2 +
                features["recency"] * 0.15 +
                features["reactions"] * 0.1 +
                features["has_code"] * 0.1 +
                features["source_diversity"] * 0.05
            )

            reranked.append({
                **candidate,
                "final_score": final_score,
                "features": features
            })

        # Sort by final score
        reranked.sort(key=lambda x: x["final_score"], reverse=True)

        return reranked

    def _recency_score(self, timestamp_str: Optional[str]) -> float:
        """Calculate recency score (exponential decay)"""
        if not timestamp_str:
            return 0.0

        try:
            from datetime import datetime
            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            age_days = (datetime.utcnow() - timestamp).days

            # Exponential decay: score = e^(-age_days / 30)
            import math
            score = math.exp(-age_days / 30.0)

            return score

        except Exception:
            return 0.0

    async def _build_metadata_filter(
        self,
        query_analysis: Dict[str, Any],
        user_id: str,
        db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """Build Pinecone metadata filter from query analysis"""

        filters = {}

        # Channel filter
        channels = query_analysis.get("channels")
        if channels:
            # Get channel IDs from names
            result = await db.execute(
                select(Channel.channel_id)
                .where(Channel.channel_name.in_(channels))
            )
            channel_ids = [row[0] for row in result.all()]

            if channel_ids:
                filters["channel_id"] = {"$in": channel_ids}

        # Temporal filter
        temporal = query_analysis.get("temporal")
        if temporal:
            if temporal.get("start_date"):
                filters["timestamp"] = {"$gte": temporal["start_date"].timestamp()}
            if temporal.get("end_date"):
                if "timestamp" in filters:
                    filters["timestamp"]["$lte"] = temporal["end_date"].timestamp()
                else:
                    filters["timestamp"] = {"$lte": temporal["end_date"].timestamp()}

        # Code filter
        if query_analysis.get("has_code_intent"):
            filters["has_code"] = True

        return filters if filters else None

    def _rewrite_query(self, query: str, query_analysis: Dict[str, Any]) -> str:
        """Rewrite conversational query to improve semantic matching"""
        import re

        # Remove common conversational prefixes
        rewritten = query.lower()

        # Remove question words and conversational phrases
        conversational_patterns = [
            r'^(can you |could you |please |would you |do you have |where is |what is |show me |find |get |fetch )',
            r'(please|thanks|thank you)$',
            r'\?$'
        ]

        for pattern in conversational_patterns:
            rewritten = re.sub(pattern, '', rewritten, flags=re.IGNORECASE)

        rewritten = rewritten.strip()

        # If the query analysis has entities, include them
        entities = query_analysis.get("entities", [])
        if entities:
            entity_texts = [e["text"] for e in entities if e.get("text")]
            if entity_texts:
                # Add entity context to improve matching
                rewritten = f"{rewritten} {' '.join(entity_texts)}"

        # If it's a code query, emphasize that
        if query_analysis.get("has_code_intent"):
            rewritten = f"code {rewritten}"

        # Clean up extra spaces
        rewritten = ' '.join(rewritten.split())

        return rewritten if rewritten else query

    async def _hydrate_results(
        self,
        results: List[Dict[str, Any]],
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """Hydrate results with channel names, user names, and message text"""
        from sqlalchemy import select
        from app.models.message import Message

        # Extract message IDs
        message_ids = [r.get("message_id") for r in results if r.get("message_id")]

        if not message_ids:
            return results

        # Fetch messages from database in one query
        stmt = select(Message).where(Message.message_id.in_(message_ids))
        result = await db.execute(stmt)
        messages_db = result.scalars().all()

        # Create lookup map
        message_map = {msg.message_id: msg for msg in messages_db}

        # Hydrate results
        hydrated = []
        for result in results:
            msg_id = result.get("message_id")
            msg_db = message_map.get(msg_id)

            if msg_db:
                # Add all the human-readable fields
                result["channel_name"] = msg_db.channel_name
                result["channel_id"] = msg_db.channel_id
                result["user_name"] = msg_db.user_name
                result["user_id"] = msg_db.user_id
                result["text"] = msg_db.text
                result["timestamp"] = msg_db.timestamp.isoformat() if msg_db.timestamp else ""
            else:
                # Fallback to metadata if available
                metadata = result.get("metadata", {})
                result["channel_name"] = metadata.get("channel_name", "unknown")
                result["channel_id"] = metadata.get("channel_id", "")
                result["user_name"] = metadata.get("user_name", "unknown")
                result["user_id"] = metadata.get("user_id", "")
                result["text"] = metadata.get("text", "")
                result["timestamp"] = metadata.get("timestamp", "")

            hydrated.append(result)

        return hydrated


# Global retrieval service instance
retrieval_service = RetrievalService()
