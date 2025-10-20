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
        team_id: str,  # ✅ REQUIRED - enforces permission filtering
        db: AsyncSession,
        top_k: int = 50
    ) -> List[Dict[str, Any]]:
        """Main retrieval pipeline with multiple strategies

        SECURITY: team_id is REQUIRED to enforce permission filtering.
        All results are filtered to only include channels the user has access to.
        """

        logger.info("retrieval_started", query=query[:100], team_id=team_id)

        # Validate team_id is provided
        if not team_id:
            logger.error("team_id_required_but_missing", user_id=user_id)
            raise ValueError("team_id is required for permission filtering")

        # Stage 1: Parallel candidate generation
        start_time = time.time()

        # Run all retrieval strategies in parallel
        semantic_results, keyword_results, entity_results = await self._parallel_retrieval(
            query,
            query_analysis,
            user_id,
            db,
            team_id
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

        # Stage 5: ALWAYS filter by user permissions (SECURITY CRITICAL)
        from app.services.permission_service import permission_service
        filtered_results = await permission_service.filter_results_by_permissions(
            team_id=team_id,
            user_id=user_id,
            results=hydrated_results,
            db=db
        )

        logger.info(
            "permission_filtering_applied",
            user_id=user_id,
            team_id=team_id,
            results_before=len(hydrated_results),
            results_after=len(filtered_results),
            filtered_count=len(hydrated_results) - len(filtered_results)
        )

        return filtered_results

    async def _parallel_retrieval(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        user_id: str,
        db: AsyncSession,
        team_id: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run multiple retrieval strategies in parallel

        SECURITY: team_id is REQUIRED for permission filtering
        """

        import asyncio

        # Execute all strategies concurrently
        semantic_task = self._semantic_search(query, query_analysis, user_id, db, team_id)
        keyword_task = self._keyword_search(query, query_analysis, user_id, db, team_id)
        entity_task = self._entity_based_search(query, query_analysis, user_id, db, team_id)

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
        db: AsyncSession,
        team_id: str
    ) -> List[Dict[str, Any]]:
        """Semantic vector search using Pinecone (messages + conditional file search)

        SECURITY: team_id is REQUIRED for permission filtering
        """

        logger.info("DEBUG_semantic_search_called", query=query[:100])

        try:
            # Rewrite query to improve semantic matching
            search_query = self._rewrite_query(query, query_analysis)
            logger.info("query_rewritten", original=query[:100], rewritten=search_query[:100])

            # Expand query for better recall (if enabled)
            from app.services.query_expander import query_expander
            query_variations = await query_expander.expand_query(search_query, max_expansions=2)

            # ENHANCEMENT: Add knowledge graph-based query expansion
            # Extract entities and expand using knowledge graph relationships
            kg_expanded_queries = await self._expand_query_with_knowledge_graph(
                search_query,
                query_analysis,
                db
            )

            # Combine all query variations (original expansions + knowledge graph expansions)
            if kg_expanded_queries:
                query_variations.extend(kg_expanded_queries)
                logger.info(
                    "knowledge_graph_expansion",
                    original_variations=len(query_variations) - len(kg_expanded_queries),
                    kg_variations=len(kg_expanded_queries),
                    total_variations=len(query_variations)
                )

            # Detect if user wants files
            file_intent = self._detect_file_intent(query, query_analysis)
            logger.info("DEBUG_file_intent_detected", file_intent=file_intent, query=query[:100])

            # Build metadata filter for messages
            metadata_filter = await self._build_metadata_filter(
                query_analysis,
                user_id,
                db,
                team_id
            )

            # STEP 1: Search messages
            message_results = await self._search_message_vectors(
                query_variations,
                metadata_filter,
                top_k_per_variation=50
            )

            # STEP 2: Conditionally search files
            file_results = []

            logger.info("DEBUG_checking_file_search_conditions",
                       explicit_request=file_intent["explicit_request"],
                       message_count=len(message_results))

            if file_intent["explicit_request"]:
                # User explicitly asked for files - search with normal threshold
                logger.info("file_search_triggered", reason="explicit_request", query=query[:50])
                logger.info("DEBUG_entering_explicit_file_search")
                file_results = await self._search_file_vectors(
                    query_variations,
                    metadata_filter,
                    top_k_per_variation=20,
                    min_score=0.25
                )
                logger.info("DEBUG_file_search_completed", file_count=len(file_results))
            elif len(message_results) < 3 or (message_results and max(r["score"] for r in message_results) < 0.6):
                # Fallback: poor message results - try files with higher threshold
                logger.info("file_search_triggered", reason="fallback_poor_results", message_count=len(message_results))
                logger.info("DEBUG_entering_fallback_file_search")
                file_results = await self._search_file_vectors(
                    query_variations,
                    metadata_filter,
                    top_k_per_variation=10,
                    min_score=0.5
                )
                logger.info("DEBUG_file_search_completed", file_count=len(file_results))
            else:
                logger.info("DEBUG_file_search_skipped", reason="no_conditions_met")

            # STEP 3: Code Intelligence - Search code snippets if code intent detected
            code_results = []

            if settings.enable_code_intelligence:
                from app.services.code_query_analyzer import code_query_analyzer
                from app.services.code_retrieval_service import code_retrieval_service

                # Analyze if this is a code-specific query
                code_intent = code_query_analyzer.analyze_code_intent(query)

                logger.info(
                    "code_intent_analysis",
                    is_code_query=code_intent["is_code_query"],
                    confidence=code_intent["confidence"],
                    code_intent_type=code_intent.get("code_intent")
                )

                # If code query detected (threshold 0.3 - same as detection threshold), search code namespace
                if code_intent["is_code_query"] and code_intent["confidence"] >= 0.3:
                    logger.info("code_search_triggered", query=query[:50], confidence=code_intent["confidence"])
                    code_results = await code_retrieval_service.retrieve_code(
                        query=query,
                        code_intent=code_intent,
                        db=db,
                        top_k=15  # Get top 15 code snippets
                    )
                    logger.info("code_search_completed", code_count=len(code_results))

            # Combine and deduplicate results (messages + files + code)
            all_results = message_results + file_results + code_results
            all_results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            results = all_results[:100]  # Top 100

            logger.info(
                "semantic_search_completed",
                query_variations=len(query_variations),
                message_results=len(message_results),
                file_results=len(file_results),
                code_results=len(code_results),
                total_results=len(results),
                file_intent=file_intent
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
        db: AsyncSession,
        team_id: str
    ) -> List[Dict[str, Any]]:
        """Keyword-based search using database

        SECURITY: team_id is REQUIRED for permission filtering
        """

        try:
            # Build SQL query with REQUIRED team_id filter
            query_filters = [
                Message.text.ilike(f"%{query}%"),
                Message.team_id == team_id  # ✅ ALWAYS filter by team_id
            ]

            # Add channel filter - distinguish between IDs (uppercase) and names (lowercase)
            if query_analysis.get("channels"):
                channel_values = query_analysis["channels"]

                # Separate channel IDs (uppercase, starts with 'C') from channel names (lowercase)
                channel_ids = [ch for ch in channel_values if ch and ch[0].isupper() and ch.startswith('C')]
                channel_names = [ch for ch in channel_values if ch and ch not in channel_ids]

                # Build filter: (channel_id IN (...) OR channel_name IN (...))
                channel_conditions = []
                if channel_ids:
                    channel_conditions.append(Message.channel_id.in_(channel_ids))
                if channel_names:
                    channel_conditions.append(Message.channel_name.in_(channel_names))

                if channel_conditions:
                    query_filters.append(or_(*channel_conditions))

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
        db: AsyncSession,
        team_id: str
    ) -> List[Dict[str, Any]]:
        """Entity-based retrieval using extracted entities and knowledge graph

        SECURITY: team_id is REQUIRED for permission filtering
        """

        try:
            entities = query_analysis.get("entities", [])

            if not entities:
                return []

            # Extract entity texts
            entity_texts = [e["text"] for e in entities]

            # ENHANCEMENT: Expand entity search using knowledge graph
            expanded_entities = await self._expand_entities_with_graph(entity_texts, db)
            all_entity_texts = list(set(entity_texts + expanded_entities))

            logger.info(
                "entity_expansion",
                original_count=len(entity_texts),
                expanded_count=len(all_entity_texts),
                new_entities=expanded_entities[:5]
            )

            # Find messages containing these entities (original + related)
            # ALWAYS include team_id filter
            query_filters = [Message.team_id == team_id]  # ✅ REQUIRED team_id filter

            for entity_text in all_entity_texts:
                query_filters.append(Message.text.ilike(f"%{entity_text}%"))

            if not query_filters:
                return []

            result = await db.execute(
                select(Message)
                .where(or_(*query_filters))
                .order_by(Message.importance_score.desc())
                .limit(50)  # Increased limit due to expansion
            )

            messages = result.scalars().all()

            # Score based on entity matches (original entities weighted higher)
            results = []
            for msg in messages:
                # Count how many original entities match
                original_match_count = sum(
                    1 for entity in entity_texts
                    if entity.lower() in (msg.text or "").lower()
                )

                # Count related entity matches
                related_match_count = sum(
                    1 for entity in expanded_entities
                    if entity.lower() in (msg.text or "").lower()
                )

                # Higher weight for original entities
                score = (
                    (original_match_count / len(entity_texts) * 0.5) +
                    (related_match_count / max(len(expanded_entities), 1) * 0.2) +
                    (msg.importance_score / 10.0 * 0.3)
                )

                results.append({
                    "message_id": msg.message_id,
                    "score": score,
                    "source": "entity_graph",
                    "metadata": {
                        "channel_id": msg.channel_id,
                        "channel_name": msg.channel_name,
                        "user_id": msg.user_id,
                        "user_name": msg.user_name,
                        "timestamp": msg.timestamp.isoformat(),
                        "text": msg.text[:500] if msg.text else "",
                        "importance_score": msg.importance_score,
                        "matched_entities": original_match_count + related_match_count,
                        "original_matches": original_match_count,
                        "related_matches": related_match_count
                    }
                })

            logger.info("entity_graph_search_completed", results=len(results))
            return results

        except Exception as e:
            logger.error("entity_search_error", error=str(e))
            return []

    def _detect_file_intent(self, query: str, query_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Detect if user is asking for files/documents"""

        file_keywords = [
            "file", "document", "pdf", "html", "code",
            "screenshot", "image", "attachment", "download",
            "spreadsheet", "presentation", "doc", "txt",
            "csv", "excel", "powerpoint", "slide"
        ]

        query_lower = query.lower()

        # Check for explicit file keywords
        explicit_request = any(kw in query_lower for kw in file_keywords)

        # Check for file extensions
        import re
        has_extension = bool(re.search(r'\.(pdf|html|htm|doc|docx|txt|csv|xlsx|xls|ppt|pptx|png|jpg|jpeg|gif|zip|json|xml|yaml|yml)', query_lower))

        return {
            "explicit_request": explicit_request or has_extension,
            "confidence": 1.0 if (explicit_request or has_extension) else 0.0,
            "matched_keywords": [kw for kw in file_keywords if kw in query_lower]
        }

    async def _search_message_vectors(
        self,
        query_variations: List[str],
        metadata_filter: Dict[str, Any],
        top_k_per_variation: int = 50
    ) -> List[Dict[str, Any]]:
        """Search message vectors in Pinecone"""

        seen_message_ids = {}  # Track best score for each message

        # Don't add "type": "message" filter - message vectors don't have a type field in metadata
        # (only file vectors have "type": "file")
        message_filter = {**metadata_filter} if metadata_filter else {}

        logger.info(
            "pinecone_message_filter_debug",
            metadata_filter=metadata_filter,
            message_filter=message_filter
        )

        for q_variant in query_variations:
            # Generate embedding for this variation
            query_embedding = await embedding_service.generate_embedding(q_variant)

            if not query_embedding:
                continue

            # Query Pinecone for messages
            matches = vector_db_manager.query(
                vector=query_embedding,
                top_k=top_k_per_variation,
                filter_dict=message_filter,
                include_metadata=True
            )

            # Collect results, tracking best score per message
            for match in matches:
                msg_id = match.get("metadata", {}).get("message_id")
                score = match.get("score", 0.0)

                # Keep highest score for each message
                if msg_id and (msg_id not in seen_message_ids or score > seen_message_ids[msg_id]["score"]):
                    seen_message_ids[msg_id] = {
                        "message_id": msg_id,
                        "score": score,
                        "source": "semantic",
                        "result_type": "message",
                        "query_variant": q_variant if len(query_variations) > 1 else None,
                        "metadata": match.get("metadata", {})
                    }

        # Convert to list and sort by score
        results = list(seen_message_ids.values())
        results.sort(key=lambda x: x["score"], reverse=True)

        return results

    async def _search_file_vectors(
        self,
        query_variations: List[str],
        metadata_filter: Dict[str, Any],
        top_k_per_variation: int = 20,
        min_score: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Search file vectors in Pinecone"""

        logger.info("DEBUG_file_vector_search_started",
                   query_variations=len(query_variations),
                   metadata_filter=metadata_filter,
                   min_score=min_score)

        seen_file_ids = {}  # Track best score for each file

        # Build file filter - exclude message-specific fields like has_code
        file_filter = {"type": "file"}

        # Only include filters that apply to files (team_id, channel_id, timestamp)
        if metadata_filter:
            if "team_id" in metadata_filter:
                file_filter["team_id"] = metadata_filter["team_id"]
            if "channel_id" in metadata_filter:
                file_filter["channel_id"] = metadata_filter["channel_id"]
            if "timestamp" in metadata_filter:
                file_filter["timestamp"] = metadata_filter["timestamp"]

        logger.info("DEBUG_file_filter_created", file_filter=file_filter)

        for q_variant in query_variations:
            # Generate embedding for this variation
            query_embedding = await embedding_service.generate_embedding(q_variant)

            if not query_embedding:
                continue

            # Query Pinecone for files
            matches = vector_db_manager.query(
                vector=query_embedding,
                top_k=top_k_per_variation,
                filter_dict=file_filter,
                include_metadata=True
            )

            logger.info("DEBUG_pinecone_file_matches",
                       query_variant=q_variant[:50],
                       matches_count=len(matches))

            # Collect results, tracking best score per file
            for match in matches:
                file_id = match.get("metadata", {}).get("file_id")
                score = match.get("score", 0.0)

                logger.info("DEBUG_file_match_received",
                           file_id=file_id,
                           score=score,
                           min_score=min_score,
                           passes_threshold=score >= min_score)

                # Only include if score meets threshold
                if score < min_score:
                    continue

                # Keep highest score for each file
                if file_id and (file_id not in seen_file_ids or score > seen_file_ids[file_id]["score"]):
                    seen_file_ids[file_id] = {
                        "file_id": file_id,
                        "score": score,
                        "source": "semantic",
                        "result_type": "file",
                        "query_variant": q_variant if len(query_variations) > 1 else None,
                        "metadata": match.get("metadata", {})
                    }

        # Convert to list and sort by score
        results = list(seen_file_ids.values())
        results.sort(key=lambda x: x["score"], reverse=True)

        logger.info(
            "file_vector_search_completed",
            query_variations=len(query_variations),
            unique_files=len(results),
            min_score=min_score
        )

        return results

    async def _expand_query_with_knowledge_graph(
        self,
        query: str,
        query_analysis: Dict[str, Any],
        db: AsyncSession,
        max_variations: int = 3
    ) -> List[str]:
        """Generate query variations using knowledge graph entity relationships

        3-Tier Strategy:
        1. Extract entities using regex (fast)
        2. Check which ones exist in our knowledge graph DB
        3. Only expand entities that have relationships
        """

        try:
            from app.services.entity_service import entity_service
            from app.models.entity import Entity
            from sqlalchemy import select, or_

            # Tier 1: Extract potential entities using regex (fast, no API cost)
            candidate_entities = entity_service._extract_entities_regex(query)

            if not candidate_entities:
                # Fallback: try query_analysis entities
                entities = query_analysis.get("entities", [])
                if entities:
                    candidate_entities = entities

            if not candidate_entities:
                return []

            candidate_texts = [e["text"] for e in candidate_entities]

            # Tier 2: Check which candidates actually exist in our knowledge graph
            # Only expand entities we have data for (very reliable)
            entity_filters = []
            for text in candidate_texts:
                normalized = entity_service._normalize_entity(text)
                entity_filters.append(Entity.canonical_form == normalized)

            if not entity_filters:
                return []

            result = await db.execute(
                select(Entity).where(or_(*entity_filters))
            )
            found_entities = result.scalars().all()

            if not found_entities:
                logger.debug("kg_no_matching_entities", candidates=candidate_texts)
                return []

            entity_texts = [e.canonical_form for e in found_entities]

            # Tier 3: Expand using knowledge graph relationships
            expanded_entities = await self._expand_entities_with_graph(entity_texts, db, max_related=5)

            if not expanded_entities:
                logger.debug("kg_no_expansion", entities=entity_texts)
                return []

            logger.info(
                "kg_entity_expansion",
                candidates=candidate_texts,
                matched_entities=entity_texts,
                expanded_entities=expanded_entities[:5]
            )

            # Generate new query variations by replacing original entities with related ones
            query_variations = []

            # Strategy 1: Replace entities with their related terms
            for expanded_entity in expanded_entities[:max_variations]:
                # Create variation by adding expanded entity to query
                variation = f"{query} {expanded_entity}"
                query_variations.append(variation)

            # Strategy 2: If we have multiple original entities, try substitution
            if len(entity_texts) > 0 and len(expanded_entities) > 0:
                # Replace first entity with related entity
                for i, original_entity in enumerate(entity_texts[:2]):
                    for expanded_entity in expanded_entities[:2]:
                        if expanded_entity.lower() != original_entity.lower():
                            variation = query.replace(original_entity, expanded_entity)
                            if variation != query and variation not in query_variations:
                                query_variations.append(variation)

            return query_variations[:max_variations]

        except Exception as e:
            logger.error("kg_query_expansion_error", error=str(e))
            return []

    async def _expand_entities_with_graph(
        self,
        entity_texts: List[str],
        db: AsyncSession,
        max_related: int = 5
    ) -> List[str]:
        """Expand entity search using knowledge graph relationships"""

        try:
            from app.services.entity_service import entity_service
            from app.models.entity import Entity

            expanded = []

            for entity_text in entity_texts:
                # Get entity from database
                entity = await entity_service.get_entity_by_text(entity_text, db)

                if not entity:
                    continue

                # Get related entities
                related = await entity_service.get_related_entities(
                    entity.id,
                    db,
                    limit=max_related
                )

                # Add related entity texts
                for related_entity, confidence in related:
                    # Only add high-confidence relationships
                    if confidence > 0.3:
                        expanded.append(related_entity.canonical_form)

            return expanded[:15]  # Limit total expansion

        except Exception as e:
            logger.error("entity_expansion_error", error=str(e))
            return []

    def _reciprocal_rank_fusion(
        self,
        semantic_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        entity_results: List[Dict[str, Any]],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """Combine multiple ranking lists using Reciprocal Rank Fusion

        Handles message results (message_id), file results (file_id), and code results (snippet_id)
        """

        # Aggregate scores by result_id (could be message_id, file_id, or snippet_id)
        scores = {}

        # Process semantic results
        for idx, result in enumerate(semantic_results):
            # Use snippet_id for code, file_id for files, message_id for messages
            result_id = result.get("snippet_id") or result.get("file_id") or result.get("message_id")
            result_type = result.get("result_type", "message")
            rrf_score = 1 / (k + idx + 1)

            if result_id not in scores:
                scores[result_id] = {
                    "result_type": result_type,
                    "rrf_score": 0.0,
                    "sources": [],
                    "metadata": result.get("metadata", {}),
                    **result  # Preserve original result fields
                }

            scores[result_id]["rrf_score"] += rrf_score
            scores[result_id]["sources"].append("semantic")

        # Process keyword results
        for idx, result in enumerate(keyword_results):
            result_id = result.get("snippet_id") or result.get("file_id") or result.get("message_id")
            result_type = result.get("result_type", "message")
            rrf_score = 1 / (k + idx + 1)

            if result_id not in scores:
                scores[result_id] = {
                    "result_type": result_type,
                    "rrf_score": 0.0,
                    "sources": [],
                    "metadata": result.get("metadata", {}),
                    **result  # Preserve original result fields
                }

            scores[result_id]["rrf_score"] += rrf_score
            scores[result_id]["sources"].append("keyword")

        # Process entity results
        for idx, result in enumerate(entity_results):
            result_id = result.get("snippet_id") or result.get("file_id") or result.get("message_id")
            result_type = result.get("result_type", "message")
            rrf_score = 1 / (k + idx + 1)

            if result_id not in scores:
                scores[result_id] = {
                    "result_type": result_type,
                    "rrf_score": 0.0,
                    "sources": [],
                    "metadata": result.get("metadata", {}),
                    **result  # Preserve original result fields
                }

            scores[result_id]["rrf_score"] += rrf_score
            scores[result_id]["sources"].append("entity")

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
        db: AsyncSession,
        team_id: str
    ) -> Dict[str, Any]:
        """Build Pinecone metadata filter from query analysis

        SECURITY: team_id is REQUIRED for permission filtering
        Returns a filter dict (never None - always includes team_id and user's accessible channels)

        This implements TWO levels of permission filtering:
        1. PRE-FILTERING: At Pinecone query level (faster, reduces unnecessary retrieval)
        2. POST-FILTERING: After hydration in retrieve() method (backup safety net)
        """

        # STEP 1: Get user's accessible channels from permission service (PRE-FILTERING)
        from app.services.permission_service import permission_service

        permission_filter = await permission_service.build_channel_filter(
            team_id=team_id,
            user_id=user_id,
            db=db
        )

        # Start with permission-based filter (team_id + accessible channels)
        if permission_filter:
            filters = permission_filter.copy()  # {"team_id": ..., "channel_id": {"$in": [...]}}
            logger.info(
                "permission_prefilter_applied",
                team_id=team_id,
                user_id=user_id,
                accessible_channels_count=len(permission_filter.get("channel_id", {}).get("$in", []))
            )
        else:
            # Fallback: if no accessible channels found, just use team_id
            # (post-filtering will catch this and return empty results)
            filters = {"team_id": team_id}
            logger.warning(
                "no_accessible_channels_for_prefilter",
                team_id=team_id,
                user_id=user_id
            )

        # STEP 2: If query specifies specific channels, intersect with accessible channels
        channels = query_analysis.get("channels")
        if channels:
            # Separate channel IDs (uppercase, starts with 'C') from channel names (lowercase)
            channel_ids_direct = [ch for ch in channels if ch and ch[0].isupper() and ch.startswith('C')]
            channel_names = [ch for ch in channels if ch and ch not in channel_ids_direct]

            # Get channel IDs from names (if any)
            requested_channel_ids = list(channel_ids_direct)  # Start with direct IDs
            if channel_names:
                result = await db.execute(
                    select(Channel.channel_id)
                    .where(Channel.channel_name.in_(channel_names))
                )
                name_to_ids = [row[0] for row in result.all()]
                requested_channel_ids.extend(name_to_ids)

            # SECURITY: Intersect requested channels with user's accessible channels
            if requested_channel_ids and "channel_id" in filters and "$in" in filters["channel_id"]:
                accessible = set(filters["channel_id"]["$in"])
                requested = set(requested_channel_ids)
                allowed_channels = list(accessible & requested)  # Intersection

                if allowed_channels:
                    filters["channel_id"] = {"$in": allowed_channels}
                    logger.info(
                        "channel_filter_intersected",
                        requested=len(requested_channel_ids),
                        accessible=len(accessible),
                        allowed=len(allowed_channels)
                    )
                else:
                    # User requested channels they don't have access to - return no results
                    # Set filter to impossible condition
                    filters["channel_id"] = {"$in": ["IMPOSSIBLE_CHANNEL_ID"]}
                    logger.warning(
                        "user_requested_inaccessible_channels",
                        user_id=user_id,
                        requested_channels=requested_channel_ids
                    )
            elif requested_channel_ids:
                # No accessible channels restriction (shouldn't happen), use requested
                filters["channel_id"] = {"$in": requested_channel_ids}

            logger.info(
                "channel_filter_debug",
                channels_input=channels,
                channel_ids_direct=channel_ids_direct,
                channel_names=channel_names,
                requested_channel_ids=requested_channel_ids
            )

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

        return filters  # Always returns a dict with at least team_id

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
        """Hydrate results with message and file data"""
        from sqlalchemy import select
        from app.models.message import Message
        from app.models.file import File

        # Separate message and file IDs
        message_ids = [r.get("message_id") for r in results if r.get("message_id")]
        file_ids = [r.get("file_id") for r in results if r.get("file_id")]

        # Fetch messages from database
        message_map = {}
        if message_ids:
            stmt = select(Message).where(Message.message_id.in_(message_ids))
            result = await db.execute(stmt)
            messages_db = result.scalars().all()
            message_map = {msg.message_id: msg for msg in messages_db}

        # Fetch files from database
        file_map = {}
        if file_ids:
            stmt = select(File).where(File.file_id.in_(file_ids))
            result = await db.execute(stmt)
            files_db = result.scalars().all()
            file_map = {file.file_id: file for file in files_db}

        # Hydrate results
        hydrated = []
        for result in results:
            result_type = result.get("result_type", "message")

            if result_type == "file":
                # Hydrate file result
                file_id = result.get("file_id")
                file_db = file_map.get(file_id)

                if file_db:
                    result["filename"] = file_db.filename
                    result["title"] = file_db.title
                    result["filetype"] = file_db.filetype
                    result["mimetype"] = file_db.mimetype
                    result["size"] = file_db.size
                    result["extracted_text"] = file_db.extracted_text[:2000] if file_db.extracted_text else ""  # Limit text length
                    result["channel_id"] = file_db.channel_id
                    result["user_id"] = file_db.user_id
                    result["slack_url"] = file_db.slack_url
                    result["timestamp"] = file_db.slack_created_at.isoformat() if file_db.slack_created_at else ""
                else:
                    # Fallback to metadata
                    metadata = result.get("metadata", {})
                    result["filename"] = metadata.get("filename", "unknown")
                    result["filetype"] = metadata.get("filetype", "")
                    result["extracted_text"] = metadata.get("text", "")

            else:
                # Hydrate message result (existing logic)
                msg_id = result.get("message_id")
                msg_db = message_map.get(msg_id)

                if msg_db:
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
