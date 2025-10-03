"""Query API endpoints"""
import time
import uuid
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import QueryRequest, QueryResponse, MessageResult, ThreadContext, QueryAnalysis
from app.core.database import get_db
from app.core.cache import cache_manager
from app.core.logging import get_logger
from app.core.monitoring import query_requests, query_latency
from app.utils.rate_limiter import rate_limiter
from app.services.query_service import query_service
from app.services.retrieval_service import retrieval_service
from app.services.context_service import context_service

logger = get_logger(__name__)
router = APIRouter()


@router.post("/query", response_model=QueryResponse)
async def query_slack_data(
    request: QueryRequest,
    db: AsyncSession = Depends(get_db)
) -> QueryResponse:
    """Main query endpoint for Slack intelligence"""

    start_time = time.time()
    query_id = str(uuid.uuid4())

    logger.info(
        "query_received",
        query_id=query_id,
        user_id=request.user_id,
        query=request.query[:100]
    )

    try:
        # Rate limiting
        await rate_limiter.check_rate_limit(request.user_id)

        # Check cache
        cache_key = f"query:{hash(request.query)}:{request.user_id}:{request.top_k}"
        cached_result = await cache_manager.get(cache_key)

        if cached_result:
            logger.info("cache_hit", query_id=query_id)
            from app.core.monitoring import cache_hits
            cache_hits.labels(cache_level="L1").inc()

            # Update cached result with new query_id
            cached_result["query_id"] = query_id
            cached_result["from_cache"] = True

            return QueryResponse(**cached_result)

        from app.core.monitoring import cache_misses
        cache_misses.labels(cache_level="L1").inc()

        # Step 1: Query analysis
        analysis = await query_service.analyze_query(request.query)

        # Track query intent
        for intent in analysis.get("intents", []):
            query_requests.labels(intent_type=intent).inc()

        # Step 2: Multi-stage retrieval
        retrieval_results = await retrieval_service.retrieve(
            query=request.query,
            query_analysis=analysis,
            user_id=request.user_id,
            db=db,
            top_k=request.top_k
        )

        # Step 3: Context assembly (if requested)
        thread_contexts = None
        meta_context = None

        if request.include_context and retrieval_results:
            context_data = await context_service.assemble_context(
                results=retrieval_results,
                query_analysis=analysis,
                db=db,
                max_messages=50
            )

            thread_contexts = context_data["thread_contexts"]
            meta_context = context_data["meta_context"]

        # Format results
        formatted_results = []
        for result in retrieval_results[:request.top_k]:
            metadata = result.get("metadata", {})

            # Generate permalink
            channel_id = metadata.get("channel_id", "")
            message_ts = result.get("message_id", "").replace(".", "")
            permalink = f"https://slack.com/archives/{channel_id}/p{message_ts}" if channel_id else None

            formatted_results.append(
                MessageResult(
                    message_id=result.get("message_id", ""),
                    channel_id=metadata.get("channel_id", ""),
                    channel_name=metadata.get("channel_name"),
                    user_id=metadata.get("user_id", ""),
                    user_name=metadata.get("user_name"),
                    timestamp=metadata.get("timestamp", ""),
                    text=metadata.get("text"),
                    relevance_score=result.get("final_score", result.get("rrf_score", 0.0)),
                    has_code=metadata.get("has_code", False),
                    reactions=metadata.get("reactions"),
                    permalink=permalink
                )
            )

        # Calculate latency
        total_latency = int((time.time() - start_time) * 1000)
        query_latency.labels(stage="total").observe(time.time() - start_time)

        # Build response
        response_data = {
            "query_id": query_id,
            "query": request.query,
            "analysis": QueryAnalysis(
                intents=analysis.get("intents", []),
                entities=analysis.get("entities", []),
                temporal=analysis.get("temporal"),
                channels=analysis.get("channels", []),
                users=analysis.get("users", []),
                has_code_intent=analysis.get("has_code_intent", False),
                question_type=analysis.get("question_type", "unknown")
            ),
            "results": formatted_results,
            "thread_contexts": [
                ThreadContext(
                    thread_ts=ctx["thread_ts"],
                    is_thread=ctx["is_thread"],
                    channel_name=ctx.get("channel_name"),
                    messages=ctx["messages"],
                    summary=ctx.get("summary"),
                    relevance_score=ctx.get("relevance_score", 0.0),
                    metadata=ctx.get("metadata", {})
                )
                for ctx in (thread_contexts or [])
            ] if request.include_context else None,
            "meta_context": meta_context,
            "total_results": len(retrieval_results),
            "latency_ms": total_latency
        }

        # Cache result
        await cache_manager.set(
            cache_key,
            response_data,
            ttl=300  # 5 minutes
        )

        # Log query for analytics
        await _log_query(query_id, request, analysis, len(retrieval_results), total_latency, db)

        logger.info(
            "query_completed",
            query_id=query_id,
            results=len(formatted_results),
            latency_ms=total_latency
        )

        return QueryResponse(**response_data)

    except Exception as e:
        logger.error("query_error", query_id=query_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Query processing failed: {str(e)}")


@router.post("/query/feedback")
async def submit_feedback(
    query_id: str,
    rating: int,
    clicked_results: list = [],
    db: AsyncSession = Depends(get_db)
):
    """Submit user feedback for a query"""

    try:
        from app.models.query_log import QueryLog
        from sqlalchemy import update

        # Update query log with feedback
        await db.execute(
            update(QueryLog)
            .where(QueryLog.query_id == query_id)
            .values(
                user_rating=rating,
                user_clicked_results=clicked_results,
                user_copied_content=1 if rating > 3 else 0
            )
        )
        await db.commit()

        # Track metrics
        from app.core.monitoring import user_satisfaction
        user_satisfaction.labels(rating=str(rating)).inc()

        logger.info("feedback_submitted", query_id=query_id, rating=rating)

        return {"status": "success", "query_id": query_id}

    except Exception as e:
        logger.error("feedback_error", query_id=query_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


async def _log_query(
    query_id: str,
    request: QueryRequest,
    analysis: Dict[str, Any],
    result_count: int,
    latency_ms: int,
    db: AsyncSession
):
    """Log query for analytics"""

    try:
        from app.models.query_log import QueryLog

        # Determine primary intent
        intents = analysis.get("intents", [])
        primary_intent = intents[0] if intents else "unknown"

        # Extract entities
        entities = analysis.get("entities", [])

        # Temporal info
        temporal = analysis.get("temporal")
        date_start = temporal.get("start_date") if temporal else None
        date_end = temporal.get("end_date") if temporal else None

        # Channel filters
        channels = analysis.get("channels", [])

        log_entry = QueryLog(
            query_id=query_id,
            user_id=request.user_id,
            query_text=request.query,
            intent_type=primary_intent,
            entities_extracted=entities,
            date_range_start=date_start,
            date_range_end=date_end,
            channel_filters=channels,
            results_count=result_count,
            total_latency_ms=latency_ms
        )

        db.add(log_entry)
        await db.commit()

    except Exception as e:
        logger.error("query_logging_error", query_id=query_id, error=str(e))
        # Don't fail the query if logging fails
