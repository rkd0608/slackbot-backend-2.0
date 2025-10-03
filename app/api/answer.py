"""Answer generation API endpoints with streaming support"""
import time
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.logging import get_logger
from app.utils.rate_limiter import rate_limiter
from app.services.query_service import query_service
from app.services.retrieval_service import retrieval_service
from app.services.context_service import context_service
from app.services.prompt_service import prompt_service
from app.services.llm_service import llm_service
from app.services.citation_service import citation_service
from app.services.validation_service import validation_service
from app.services.conversation_service import conversation_service

logger = get_logger(__name__)
router = APIRouter()


class AnswerRequest(BaseModel):
    """Answer generation request"""
    query: str = Field(..., min_length=1, max_length=1000)
    user_id: str = Field(...)
    stream: Optional[bool] = Field(default=False, description="Enable streaming response")
    conversation_id: Optional[str] = Field(default=None, description="Continue existing conversation")
    top_k: Optional[int] = Field(default=10, ge=1, le=50)


class AnswerResponse(BaseModel):
    """Answer generation response"""
    answer_id: str
    query: str
    answer: str
    citations: list
    validation: dict
    conversation_id: str
    latency_ms: int
    usage: dict


@router.post("/answer")
async def generate_answer(
    request: AnswerRequest,
    db: AsyncSession = Depends(get_db)
):
    """Generate AI answer with citations (non-streaming)"""

    start_time = time.time()
    answer_id = str(uuid.uuid4())

    logger.info(
        "answer_request_received",
        answer_id=answer_id,
        user_id=request.user_id,
        stream=request.stream,
        has_conversation=bool(request.conversation_id)
    )

    try:
        # Rate limiting
        await rate_limiter.check_rate_limit(request.user_id)

        # Step 1: Query analysis
        analysis = await query_service.analyze_query(request.query)

        # Step 2: Retrieval
        retrieval_results = await retrieval_service.retrieve(
            query=request.query,
            query_analysis=analysis,
            user_id=request.user_id,
            db=db,
            top_k=request.top_k
        )

        if not retrieval_results:
            return AnswerResponse(
                answer_id=answer_id,
                query=request.query,
                answer="I couldn't find any relevant information in the Slack history to answer your question.",
                citations=[],
                validation={"quality_score": 0.0},
                conversation_id=request.conversation_id or "",
                latency_ms=int((time.time() - start_time) * 1000),
                usage={}
            )

        # Step 3: Context assembly
        context_data = await context_service.assemble_context(
            results=retrieval_results,
            query_analysis=analysis,
            db=db,
            max_messages=50
        )

        # Step 4: Build prompt
        prompt = prompt_service.build_prompt(
            query=request.query,
            query_analysis=analysis,
            context=context_data
        )

        # Step 5: Get conversation history if continuing
        conversation_history = []
        if request.conversation_id:
            conversation_history = await conversation_service.get_conversation_history(
                request.conversation_id,
                db
            )

        # Step 6: Generate response
        if conversation_history:
            llm_response = await llm_service.generate_with_conversation(
                prompt=prompt,
                conversation_history=conversation_history,
                stream=False
            )
        else:
            llm_response = await llm_service.generate_response(
                prompt=prompt,
                stream=False
            )

        answer_text = llm_response["content"]

        # Step 7: Extract and format citations
        processed_answer, citations = citation_service.extract_citations(answer_text)
        formatted_citations = citation_service.format_citations(
            citations,
            context_data.get("thread_contexts", [])
        )

        # Step 8: Validate response
        validation = validation_service.validate_response(
            response_text=processed_answer,
            query=request.query,
            thread_contexts=context_data.get("thread_contexts", []),
            citations=citations
        )

        # Step 9: Manage conversation
        if request.conversation_id:
            conversation_id = request.conversation_id
            await conversation_service.add_turn(conversation_id, "user", request.query, db)
            await conversation_service.add_turn(conversation_id, "assistant", processed_answer, db)
        else:
            # Create new conversation
            conversation_id = await conversation_service.create_conversation(
                request.user_id,
                request.query,
                db
            )
            await conversation_service.add_turn(conversation_id, "user", request.query, db)
            await conversation_service.add_turn(conversation_id, "assistant", processed_answer, db)

        total_latency = int((time.time() - start_time) * 1000)

        logger.info(
            "answer_generated",
            answer_id=answer_id,
            quality_score=validation["quality_score"],
            citation_count=len(formatted_citations),
            latency_ms=total_latency
        )

        return AnswerResponse(
            answer_id=answer_id,
            query=request.query,
            answer=processed_answer,
            citations=formatted_citations,
            validation=validation,
            conversation_id=conversation_id,
            latency_ms=total_latency,
            usage=llm_response.get("usage", {})
        )

    except Exception as e:
        logger.error("answer_generation_error", answer_id=answer_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Answer generation failed: {str(e)}")


@router.post("/answer/stream")
async def generate_answer_stream(
    request: AnswerRequest,
    db: AsyncSession = Depends(get_db)
):
    """Generate AI answer with streaming (Server-Sent Events)"""

    logger.info(
        "answer_stream_request",
        user_id=request.user_id,
        query=request.query[:100]
    )

    async def event_generator():
        """Generate SSE events"""
        answer_id = str(uuid.uuid4())

        try:
            # Rate limiting
            await rate_limiter.check_rate_limit(request.user_id)

            yield f"event: start\ndata: {{'answer_id': '{answer_id}'}}\n\n"

            # Query analysis
            yield f"event: status\ndata: {{'stage': 'analyzing_query'}}\n\n"
            analysis = await query_service.analyze_query(request.query)

            # Retrieval
            yield f"event: status\ndata: {{'stage': 'retrieving_context'}}\n\n"
            retrieval_results = await retrieval_service.retrieve(
                query=request.query,
                query_analysis=analysis,
                user_id=request.user_id,
                db=db,
                top_k=request.top_k
            )

            if not retrieval_results:
                yield f"event: answer\ndata: {{'text': 'No relevant information found.'}}\n\n"
                yield f"event: end\ndata: {{'answer_id': '{answer_id}'}}\n\n"
                return

            # Context assembly
            yield f"event: status\ndata: {{'stage': 'assembling_context'}}\n\n"
            context_data = await context_service.assemble_context(
                results=retrieval_results,
                query_analysis=analysis,
                db=db,
                max_messages=50
            )

            # Build prompt
            prompt = prompt_service.build_prompt(
                query=request.query,
                query_analysis=analysis,
                context=context_data
            )

            # Get conversation history
            conversation_history = []
            if request.conversation_id:
                conversation_history = await conversation_service.get_conversation_history(
                    request.conversation_id,
                    db
                )

            # Generate response with streaming
            yield f"event: status\ndata: {{'stage': 'generating_answer'}}\n\n"

            if conversation_history:
                llm_response = await llm_service.generate_with_conversation(
                    prompt=prompt,
                    conversation_history=conversation_history,
                    stream=True
                )
            else:
                llm_response = await llm_service.generate_response(
                    prompt=prompt,
                    stream=True
                )

            # Stream answer chunks
            full_answer = ""
            async for chunk in llm_response["stream"]:
                full_answer += chunk
                # Escape for JSON
                escaped_chunk = chunk.replace('"', '\\"').replace('\n', '\\n')
                yield f"event: answer\ndata: {{'text': '{escaped_chunk}'}}\n\n"

            # Extract citations
            _, citations = citation_service.extract_citations(full_answer)
            formatted_citations = citation_service.format_citations(
                citations,
                context_data.get("thread_contexts", [])
            )

            # Send citations
            import json
            citations_json = json.dumps(formatted_citations)
            yield f"event: citations\ndata: {citations_json}\n\n"

            # Manage conversation
            if request.conversation_id:
                conversation_id = request.conversation_id
            else:
                conversation_id = await conversation_service.create_conversation(
                    request.user_id,
                    request.query,
                    db
                )

            await conversation_service.add_turn(conversation_id, "user", request.query, db)
            await conversation_service.add_turn(conversation_id, "assistant", full_answer, db)

            yield f"event: end\ndata: {{'answer_id': '{answer_id}', 'conversation_id': '{conversation_id}'}}\n\n"

        except Exception as e:
            logger.error("streaming_error", error=str(e))
            yield f"event: error\ndata: {{'message': '{str(e)}'}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/conversations")
async def list_conversations(
    user_id: str,
    limit: int = 10,
    db: AsyncSession = Depends(get_db)
):
    """List user's conversations"""

    conversations = await conversation_service.get_user_conversations(
        user_id,
        db,
        limit
    )

    return {"conversations": conversations}


@router.get("/conversation/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get conversation details"""

    conversation = await conversation_service.get_conversation(conversation_id, db)

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return conversation


@router.delete("/conversation/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a conversation"""

    success = await conversation_service.delete_conversation(conversation_id, db)

    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "deleted", "conversation_id": conversation_id}
