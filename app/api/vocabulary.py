"""
Team Vocabulary API Endpoints

Provides endpoints for:
- Viewing team vocabulary
- Adding custom terms
- Disabling incorrect terms
- Seeding common abbreviations
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.services.team_vocabulary_service import TeamVocabularyService

logger = get_logger(__name__)
router = APIRouter(prefix="/api/vocabulary", tags=["vocabulary"])


# Request/Response Models
class VocabularyTermResponse(BaseModel):
    """Vocabulary term response"""
    term: str
    canonical_form: str
    term_type: str
    occurrence_count: int
    confidence_score: float
    learned_from: str
    first_seen: Optional[str]
    last_seen: Optional[str]


class AddTermRequest(BaseModel):
    """Request to add custom vocabulary term"""
    term: str = Field(..., min_length=1, max_length=200)
    canonical_form: str = Field(..., min_length=1, max_length=500)
    term_type: str = Field(default="custom", pattern="^(abbreviation|synonym|product_name|jargon|custom)$")


class ExpandQueryRequest(BaseModel):
    """Request to expand a query"""
    query: str = Field(..., min_length=1)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExpandQueryResponse(BaseModel):
    """Query expansion response"""
    original_query: str
    expanded_query: str
    expansions: List[dict]


@router.get("/{team_id}", response_model=List[VocabularyTermResponse])
async def get_team_vocabulary(
    team_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_db)
):
    """
    Get team's learned vocabulary

    Returns all active vocabulary terms for the team, sorted by usage.
    """
    try:
        service = TeamVocabularyService(db)

        vocab = await service.get_team_vocabulary(
            team_id=team_id,
            limit=limit,
            min_confidence=min_confidence
        )

        return [VocabularyTermResponse(**item) for item in vocab]

    except Exception as e:
        logger.error("get_team_vocabulary_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get vocabulary: {str(e)}"
        )


@router.post("/{team_id}/seed")
async def seed_common_abbreviations(
    team_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Seed team vocabulary with common tech abbreviations

    Adds standard abbreviations like k8s, api, db, fe, be, etc.
    Only adds terms that don't already exist.
    """
    try:
        service = TeamVocabularyService(db)

        seeded_count = await service.seed_common_abbreviations(team_id)

        logger.info("vocabulary_seeded", team_id=team_id, seeded_count=seeded_count)

        return {
            "status": "success",
            "message": f"Seeded {seeded_count} common abbreviations",
            "seeded_count": seeded_count
        }

    except Exception as e:
        logger.error("seed_vocabulary_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to seed vocabulary: {str(e)}"
        )


@router.post("/{team_id}/add")
async def add_custom_term(
    team_id: str,
    request: AddTermRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Add a custom vocabulary term

    Allows teams to manually add company-specific terms.
    """
    try:
        service = TeamVocabularyService(db)

        result = await service._record_term_mapping(
            team_id=team_id,
            term=request.term.lower(),
            canonical_form=request.canonical_form.lower(),
            term_type=request.term_type,
            learned_from="manual",
            confidence_score=1.0,  # Manual entries have high confidence
            occurrence_count=1
        )

        if result:
            logger.info(
                "custom_term_added",
                team_id=team_id,
                term=request.term,
                canonical_form=request.canonical_form
            )

            return {
                "status": "success",
                "message": "Term added successfully",
                "term": result
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to add term"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("add_custom_term_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add term: {str(e)}"
        )


@router.delete("/{team_id}/{term}")
async def disable_term(
    team_id: str,
    term: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Disable a vocabulary term

    Disables an incorrect or unwanted term mapping.
    The term will no longer be used for query expansion.
    """
    try:
        service = TeamVocabularyService(db)

        disabled = await service.disable_term(team_id, term.lower())

        if disabled:
            logger.info("vocabulary_term_disabled", team_id=team_id, term=term)

            return {
                "status": "success",
                "message": f"Term '{term}' disabled"
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Term '{term}' not found"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("disable_term_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disable term: {str(e)}"
        )


@router.post("/{team_id}/expand", response_model=ExpandQueryResponse)
async def expand_query(
    team_id: str,
    request: ExpandQueryRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Expand a query with team vocabulary

    Replaces abbreviations with their canonical forms.
    Useful for testing what would happen to a user's query.
    """
    try:
        service = TeamVocabularyService(db)

        result = await service.expand_query(
            team_id=team_id,
            query=request.query,
            min_confidence=request.min_confidence
        )

        return ExpandQueryResponse(**result)

    except Exception as e:
        logger.error("expand_query_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to expand query: {str(e)}"
        )


@router.post("/{team_id}/learn")
async def learn_from_text(
    team_id: str,
    text: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db)
):
    """
    Learn vocabulary from a text snippet

    Manually trigger vocabulary learning from a message or document.
    Useful for training the system on existing content.
    """
    try:
        service = TeamVocabularyService(db)

        learned = await service.learn_from_message(
            team_id=team_id,
            message=text
        )

        logger.info(
            "vocabulary_learned_from_text",
            team_id=team_id,
            learned_count=len(learned)
        )

        return {
            "status": "success",
            "message": f"Learned {len(learned)} terms",
            "learned": learned
        }

    except Exception as e:
        logger.error("learn_from_text_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to learn from text: {str(e)}"
        )


@router.get("/{team_id}/stats")
async def get_vocabulary_stats(
    team_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get vocabulary learning statistics for a team

    Returns counts by type, most used terms, etc.
    """
    try:
        service = TeamVocabularyService(db)

        vocab = await service.get_team_vocabulary(team_id, limit=1000)

        # Calculate statistics
        by_type = {}
        by_source = {}
        total_usage = 0

        for item in vocab:
            # Count by type
            term_type = item['term_type']
            by_type[term_type] = by_type.get(term_type, 0) + 1

            # Count by source
            source = item['learned_from']
            by_source[source] = by_source.get(source, 0) + 1

            # Total usage
            total_usage += item['occurrence_count']

        # Top 10 most used
        top_terms = sorted(vocab, key=lambda x: x['occurrence_count'], reverse=True)[:10]

        return {
            "team_id": team_id,
            "total_terms": len(vocab),
            "total_usage": total_usage,
            "by_type": by_type,
            "by_source": by_source,
            "top_terms": top_terms
        }

    except Exception as e:
        logger.error("get_vocabulary_stats_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get stats: {str(e)}"
        )
