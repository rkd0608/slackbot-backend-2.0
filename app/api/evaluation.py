"""Evaluation API endpoints for golden test management"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import List, Optional

from app.core.database import get_db
from app.services.evaluation_service import evaluation_service
from app.core.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


# Request/Response models
class CreateGoldenTestRequest(BaseModel):
    query: str = Field(..., description="Test query")
    expected_message_ids: List[str] = Field(..., description="Expected message IDs to retrieve")
    test_name: Optional[str] = Field(None, description="Name for this test")
    query_type: Optional[str] = Field(None, description="Query type (factual, code, summary, etc.)")
    expected_answer: Optional[str] = Field(None, description="Reference answer")
    category: Optional[str] = Field(None, description="Test category")
    difficulty: str = Field("medium", description="Difficulty level")
    min_relevance_score: float = Field(0.7, description="Minimum precision threshold")
    required_in_top_k: int = Field(5, description="Expected messages must appear in top K")


class RunEvaluationRequest(BaseModel):
    run_name: Optional[str] = Field(None, description="Name for this evaluation run")
    model_version: Optional[str] = Field(None, description="Model version being tested")
    category_filter: Optional[str] = Field(None, description="Filter tests by category")
    top_k: int = Field(10, description="Number of results to retrieve")


@router.post("/evaluation/golden-test")
async def create_golden_test(
    request: CreateGoldenTestRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create a new golden test case"""
    try:
        test_id = await evaluation_service.create_golden_test(
            query=request.query,
            expected_message_ids=request.expected_message_ids,
            test_name=request.test_name,
            query_type=request.query_type,
            expected_answer=request.expected_answer,
            category=request.category,
            difficulty=request.difficulty,
            min_relevance_score=request.min_relevance_score,
            required_in_top_k=request.required_in_top_k,
            db=db
        )

        return {
            "status": "success",
            "test_id": test_id,
            "message": "Golden test created successfully"
        }

    except Exception as e:
        logger.error("create_golden_test_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/evaluation/run")
async def run_evaluation(
    request: RunEvaluationRequest,
    db: AsyncSession = Depends(get_db)
):
    """Run evaluation against all active golden tests"""
    try:
        run_id = await evaluation_service.run_evaluation(
            db=db,
            run_name=request.run_name,
            model_version=request.model_version,
            category_filter=request.category_filter,
            top_k=request.top_k
        )

        if not run_id:
            raise HTTPException(status_code=404, detail="No active tests found")

        # Get summary
        summary = await evaluation_service.get_evaluation_summary(run_id, db)

        return {
            "status": "success",
            "run_id": run_id,
            "summary": summary
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("run_evaluation_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/evaluation/run/{run_id}")
async def get_evaluation_run(
    run_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get evaluation run summary"""
    try:
        summary = await evaluation_service.get_evaluation_summary(run_id, db)

        if not summary:
            raise HTTPException(status_code=404, detail="Evaluation run not found")

        return summary

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_evaluation_run_error", error=str(e), run_id=run_id)
        raise HTTPException(status_code=500, detail=str(e))
