"""Integration Status API Endpoints

Provides real-time status of integration indexing and graph building.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any

from app.core.database import get_db
from app.core.logging import get_logger
from app.core.auth import get_current_user
from app.models.user import User
from app.services.bot_readiness_service import get_bot_readiness_service
from app.services.integration_orchestrator import get_integration_orchestrator

router = APIRouter(prefix="/api/integration", tags=["integration"])
logger = get_logger(__name__)


@router.get("/status")
async def get_integration_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get comprehensive integration status for current workspace

    Returns:
        - Overall integration status
        - Per-source indexing progress
        - Graph building progress
        - Bot readiness
        - Available features
        - ETA for completion
    """
    try:
        orchestrator = get_integration_orchestrator(db)
        readiness_service = get_bot_readiness_service(db)

        # Get integration overview from orchestrator
        overview = await orchestrator.get_integration_overview(current_user.team_id)

        # Get bot readiness status
        readiness = await readiness_service.get_readiness_status(current_user.team_id)

        # Combine into comprehensive response
        return {
            "team_id": current_user.team_id,
            "overall_status": overview.get("overall_status", "unknown"),
            "overall_progress": overview.get("overall_progress", {}),
            "sources": overview.get("sources", {}),
            "graph": overview.get("graph", {}),
            "bot_ready": readiness.get("is_ready", False),
            "bot_phase": readiness.get("phase", "unknown"),
            "available_features": readiness.get("available_features", []),
            "unavailable_features": readiness.get("unavailable_features", []),
            "status_message": readiness.get("status_message", ""),
            "setup_progress": readiness.get("setup_progress", {}),
            "next_steps": overview.get("next_steps", [])
        }

    except Exception as e:
        logger.error(
            "get_integration_status_error",
            error=str(e),
            team_id=current_user.team_id
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve integration status"
        )


@router.get("/readiness")
async def check_bot_readiness(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Check if bot is ready to answer queries

    Query params (optional):
        - query_type: Type of query to check (slack_search, code_search, cross_source)

    Returns:
        - is_ready: Boolean
        - available_query_types: List of available query types
        - message: Status message if not ready
    """
    try:
        readiness_service = get_bot_readiness_service(db)

        # Check overall readiness
        is_ready = await readiness_service.is_bot_ready(current_user.team_id)

        # Check specific query types
        slack_ready = await readiness_service.is_bot_ready(
            current_user.team_id,
            query_type="slack_search"
        )
        code_ready = await readiness_service.is_bot_ready(
            current_user.team_id,
            query_type="code_search"
        )
        cross_source_ready = await readiness_service.is_bot_ready(
            current_user.team_id,
            query_type="cross_source"
        )

        available_query_types = []
        if slack_ready:
            available_query_types.append("slack_search")
        if code_ready:
            available_query_types.append("code_search")
        if cross_source_ready:
            available_query_types.append("cross_source")

        # Get message if not ready
        message = None
        if not is_ready:
            message = await readiness_service.get_not_ready_message(current_user.team_id)

        return {
            "team_id": current_user.team_id,
            "is_ready": is_ready,
            "available_query_types": available_query_types,
            "message": message,
            "capabilities": {
                "slack_search": slack_ready,
                "code_search": code_ready,
                "cross_source_queries": cross_source_ready
            }
        }

    except Exception as e:
        logger.error(
            "check_bot_readiness_error",
            error=str(e),
            team_id=current_user.team_id
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to check bot readiness"
        )


@router.get("/progress/{source_type}")
async def get_source_progress(
    source_type: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get detailed progress for a specific integration source

    Args:
        source_type: "slack" or "github"

    Returns:
        Detailed progress information for the source
    """
    try:
        if source_type not in ["slack", "github"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid source_type. Must be 'slack' or 'github'"
            )

        from app.models.integration_status import IntegrationStatus
        from sqlalchemy import select, and_

        # Get integration status
        stmt = select(IntegrationStatus).where(
            and_(
                IntegrationStatus.team_id == current_user.team_id,
                IntegrationStatus.source_type == source_type
            )
        )
        result = await db.execute(stmt)
        status = result.scalar_one_or_none()

        if not status:
            return {
                "team_id": current_user.team_id,
                "source_type": source_type,
                "is_connected": False,
                "indexing_status": "not_started",
                "progress_percentage": 0.0,
                "message": f"{source_type.capitalize()} is not connected yet"
            }

        # Calculate ETA if in progress
        eta_minutes = None
        if status.indexing_status == "in_progress" and status.progress_percentage > 0:
            # Simple ETA calculation
            if status.progress_percentage < 100:
                remaining_percentage = 100 - status.progress_percentage
                # Rough estimate: assume current rate continues
                eta_minutes = int(remaining_percentage / 2)  # ~2% per minute estimate

        return {
            "team_id": current_user.team_id,
            "source_type": source_type,
            "is_connected": status.is_connected == "true",
            "connected_at": status.connected_at.isoformat() if status.connected_at else None,
            "indexing_status": status.indexing_status,
            "indexing_started_at": status.indexing_started_at.isoformat() if status.indexing_started_at else None,
            "indexing_completed_at": status.indexing_completed_at.isoformat() if status.indexing_completed_at else None,
            "progress_percentage": status.progress_percentage,
            "total_items": status.total_items,
            "items_indexed": status.items_indexed,
            "items_failed": status.items_failed,
            "messages_indexed": status.messages_indexed,
            "files_indexed": status.files_indexed,
            "entities_extracted": status.entities_extracted,
            "eta_minutes": eta_minutes,
            "last_checkpoint": status.last_checkpoint,
            "error": status.indexing_error if status.indexing_status == "failed" else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "get_source_progress_error",
            error=str(e),
            team_id=current_user.team_id,
            source_type=source_type
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get progress for {source_type}"
        )


@router.get("/graph/status")
async def get_graph_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get cross-source graph building status

    Returns:
        Graph building progress and edge statistics
    """
    try:
        from app.models.integration_status import CrossSourceGraph
        from sqlalchemy import select

        # Get graph status
        stmt = select(CrossSourceGraph).where(
            CrossSourceGraph.team_id == current_user.team_id
        )
        result = await db.execute(stmt)
        graph_status = result.scalar_one_or_none()

        if not graph_status:
            return {
                "team_id": current_user.team_id,
                "graph_status": "not_started",
                "message": "Graph building will start after all sources are indexed"
            }

        # Calculate progress percentage
        progress_percentage = 0.0
        if graph_status.total_edges_to_build > 0:
            progress_percentage = (
                graph_status.edges_built / graph_status.total_edges_to_build
            ) * 100

        return {
            "team_id": current_user.team_id,
            "graph_status": graph_status.graph_status,
            "building_started_at": graph_status.graph_building_started_at.isoformat() if graph_status.graph_building_started_at else None,
            "building_completed_at": graph_status.graph_building_completed_at.isoformat() if graph_status.graph_building_completed_at else None,
            "progress_percentage": progress_percentage,
            "total_edges_to_build": graph_status.total_edges_to_build,
            "edges_built": graph_status.edges_built,
            "edges_failed": graph_status.edges_failed,
            "edge_statistics": {
                "slack_to_github": graph_status.slack_to_github_edges,
                "github_to_slack": graph_status.github_to_slack_edges,
                "user_identity_links": graph_status.user_identity_links
            },
            "last_full_rebuild": graph_status.last_full_rebuild.isoformat() if graph_status.last_full_rebuild else None,
            "last_incremental_update": graph_status.last_incremental_update.isoformat() if graph_status.last_incremental_update else None
        }

    except Exception as e:
        logger.error(
            "get_graph_status_error",
            error=str(e),
            team_id=current_user.team_id
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get graph building status"
        )


@router.post("/retry/{source_type}")
async def retry_failed_indexing(
    source_type: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Retry failed indexing job for a source

    This will resume from the last checkpoint if available

    Args:
        source_type: "slack" or "github"
    """
    try:
        if source_type not in ["slack", "github"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid source_type. Must be 'slack' or 'github'"
            )

        # Only admins can retry
        if not current_user.is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only workspace admins can retry indexing"
            )

        from app.models.integration_status import IntegrationStatus
        from sqlalchemy import select, and_

        # Check if indexing actually failed
        stmt = select(IntegrationStatus).where(
            and_(
                IntegrationStatus.team_id == current_user.team_id,
                IntegrationStatus.source_type == source_type
            )
        )
        result = await db.execute(stmt)
        status = result.scalar_one_or_none()

        if not status:
            raise HTTPException(
                status_code=404,
                detail=f"{source_type.capitalize()} integration not found"
            )

        if status.indexing_status != "failed":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot retry indexing - current status is '{status.indexing_status}'"
            )

        # Queue retry job
        orchestrator = get_integration_orchestrator(db)

        if source_type == "slack":
            from app.models.workspace import Workspace
            workspace_stmt = select(Workspace).where(Workspace.team_id == current_user.team_id)
            workspace_result = await db.execute(workspace_stmt)
            workspace = workspace_result.scalar_one_or_none()

            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")

            result = await orchestrator.handle_slack_installation(
                team_id=current_user.team_id,
                bot_token=workspace.bot_access_token,
                workspace=workspace
            )
        else:  # github
            from app.models.github_connection import GitHubConnection
            github_stmt = select(GitHubConnection).where(
                and_(
                    GitHubConnection.team_id == current_user.team_id,
                    GitHubConnection.user_id == current_user.user_id
                )
            )
            github_result = await db.execute(github_stmt)
            github_conn = github_result.scalar_one_or_none()

            if not github_conn:
                raise HTTPException(status_code=404, detail="GitHub connection not found")

            result = await orchestrator.handle_github_connection(
                team_id=current_user.team_id,
                github_access_token=github_conn.access_token,
                selected_repos=None
            )

        logger.info(
            "indexing_retry_queued",
            team_id=current_user.team_id,
            source_type=source_type,
            job_id=result.get("job_id")
        )

        return {
            "success": True,
            "message": f"Retry queued for {source_type} indexing",
            "job_id": result.get("job_id"),
            "will_resume_from_checkpoint": status.last_checkpoint is not None,
            "checkpoint": status.last_checkpoint
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "retry_indexing_error",
            error=str(e),
            team_id=current_user.team_id,
            source_type=source_type
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retry {source_type} indexing"
        )
