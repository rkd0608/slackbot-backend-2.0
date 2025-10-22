"""GitHub repository sync/indexing API endpoints"""
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from pydantic import BaseModel
from datetime import datetime
import uuid

from app.core.database import get_db
from app.core.logging import get_logger
from app.core.auth import get_current_user
from app.models.user import User
from app.models.integration import IntegrationSyncJob, UserIntegrationConnection, ExternalContent
from app.services.github_services.github_oauth_service import get_github_oauth_service
from app.services.github_services.github_indexing_service import get_github_indexing_service
from app.services.github_services.github_code_embedding_service import get_github_code_embedding_service

router = APIRouter(prefix="/api/v1/github", tags=["github-sync"])
logger = get_logger(__name__)


# Request/Response Models
class IndexRepositoryRequest(BaseModel):
    owner: str
    repo: str
    generate_embeddings: bool = True


class SyncJobResponse(BaseModel):
    id: str
    status: str
    integration_type: str
    sync_type: str
    target_resource: str
    total_items: int
    processed_items: int
    failed_items: int
    items_created: int
    items_updated: int
    started_at: Optional[str]
    completed_at: Optional[str]
    error_message: Optional[str]


class RepositoryInfo(BaseModel):
    full_name: str
    description: Optional[str]
    language: Optional[str]
    visibility: str
    stars: int
    forks: int
    updated_at: str


# Background task for repository indexing
async def index_repository_task(
    db: AsyncSession,
    connection_id: str,
    repo_owner: str,
    repo_name: str,
    sync_job_id: str,
    generate_embeddings: bool
):
    """Background task to index a repository"""
    from sqlalchemy import select
    try:
        # Get connection
        result = await db.execute(
            select(UserIntegrationConnection).where(
                UserIntegrationConnection.id == connection_id
            )
        )
        connection = result.scalar_one_or_none()

        if not connection:
            logger.error("connection_not_found", connection_id=connection_id)
            return

        # Index repository
        indexing_service = get_github_indexing_service(db)
        stats = await indexing_service.index_repository(
            connection=connection,
            repo_owner=repo_owner,
            repo_name=repo_name,
            sync_job_id=sync_job_id
        )

        # Generate embeddings if requested
        if generate_embeddings and stats['files_indexed'] > 0:
            logger.info(
                "starting_embedding_generation",
                team_id=connection.team_id,
                files=stats['files_indexed']
            )

            embedding_service = get_github_code_embedding_service(db)
            embedding_stats = await embedding_service.batch_embed_github_contents(
                team_id=connection.team_id,
                limit=stats['files_indexed']
            )

            logger.info(
                "embedding_generation_completed",
                team_id=connection.team_id,
                stats=embedding_stats
            )

    except Exception as e:
        logger.error(
            "index_repository_task_failed",
            repo=f"{repo_owner}/{repo_name}",
            error=str(e)
        )


@router.get("/repositories")
async def list_accessible_repositories(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List all GitHub repositories accessible to the current user

    Requires active GitHub connection
    """
    try:
        github_oauth = get_github_oauth_service()

        # Get user's GitHub connection
        connection = await github_oauth.get_user_connection(
            db=db,
            user_id=current_user.user_id,
            team_id=current_user.team_id
        )

        if not connection:
            raise HTTPException(
                status_code=404,
                detail="No active GitHub connection. Please connect your GitHub account first."
            )

        # List repositories
        indexing_service = get_github_indexing_service(db)
        repos = await indexing_service.list_user_repos(connection)

        # Format response
        repo_list = [
            RepositoryInfo(
                full_name=repo['full_name'],
                description=repo.get('description'),
                language=repo.get('language'),
                visibility='public' if not repo.get('private') else 'private',
                stars=repo.get('stargazers_count', 0),
                forks=repo.get('forks_count', 0),
                updated_at=repo['updated_at']
            )
            for repo in repos
        ]

        logger.info(
            "repositories_listed",
            user_id=current_user.user_id,
            count=len(repo_list)
        )

        return {
            "repositories": repo_list,
            "total": len(repo_list)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("list_repositories_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to list repositories: {str(e)}")


@router.get("/indexed-repositories")
async def list_indexed_repositories(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List repositories that have been indexed for the workspace

    Returns repository names and file counts
    """
    try:
        indexing_service = get_github_indexing_service(db)
        indexed_repos = await indexing_service.get_indexed_repos(current_user.team_id)

        logger.info(
            "indexed_repositories_listed",
            team_id=current_user.team_id,
            count=len(indexed_repos)
        )

        return {
            "repositories": indexed_repos,
            "total": len(indexed_repos)
        }

    except Exception as e:
        logger.error("list_indexed_repositories_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to list indexed repositories: {str(e)}")


@router.post("/index-repository")
async def index_repository(
    request: IndexRepositoryRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Index a GitHub repository

    Creates a background job to:
    1. Fetch all code files from the repository
    2. Store them in the database with permission metadata
    3. Optionally generate embeddings for semantic search

    Returns the sync job ID for monitoring progress
    """
    try:
        github_oauth = get_github_oauth_service()

        # Get user's GitHub connection
        connection = await github_oauth.get_user_connection(
            db=db,
            user_id=current_user.user_id,
            team_id=current_user.team_id
        )

        if not connection:
            raise HTTPException(
                status_code=404,
                detail="No active GitHub connection"
            )

        # Create sync job
        sync_job = IntegrationSyncJob(
            id=str(uuid.uuid4()),
            team_id=current_user.team_id,
            user_connection_id=connection.id,
            integration_type="github",
            sync_type="full",
            target_resource=f"repo:{request.owner}/{request.repo}",
            status="pending",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        db.add(sync_job)
        await db.commit()
        await db.refresh(sync_job)

        # Start background indexing task
        background_tasks.add_task(
            index_repository_task,
            db=db,
            connection_id=connection.id,
            repo_owner=request.owner,
            repo_name=request.repo,
            sync_job_id=sync_job.id,
            generate_embeddings=request.generate_embeddings
        )

        logger.info(
            "repository_indexing_started",
            sync_job_id=sync_job.id,
            repo=f"{request.owner}/{request.repo}",
            user_id=current_user.user_id
        )

        return {
            "sync_job_id": sync_job.id,
            "status": "pending",
            "message": f"Started indexing {request.owner}/{request.repo}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("index_repository_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to start repository indexing: {str(e)}")


@router.get("/sync-jobs/{job_id}")
async def get_sync_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get status of a sync job

    Returns detailed progress information
    """
    from sqlalchemy import select, and_
    try:
        result = await db.execute(
            select(IntegrationSyncJob).where(
                and_(
                    IntegrationSyncJob.id == job_id,
                    IntegrationSyncJob.team_id == current_user.team_id
                )
            )
        )
        sync_job = result.scalar_one_or_none()

        if not sync_job:
            raise HTTPException(status_code=404, detail="Sync job not found")

        return SyncJobResponse(
            id=sync_job.id,
            status=sync_job.status,
            integration_type=sync_job.integration_type,
            sync_type=sync_job.sync_type,
            target_resource=sync_job.target_resource or "",
            total_items=sync_job.total_items or 0,
            processed_items=sync_job.processed_items or 0,
            failed_items=sync_job.failed_items or 0,
            items_created=sync_job.items_created or 0,
            items_updated=sync_job.items_updated or 0,
            started_at=sync_job.started_at.isoformat() if sync_job.started_at else None,
            completed_at=sync_job.completed_at.isoformat() if sync_job.completed_at else None,
            error_message=sync_job.error_message
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_sync_job_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get sync job status: {str(e)}")


@router.get("/sync-jobs")
async def list_sync_jobs(
    status: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List sync jobs for the workspace

    Optionally filter by status
    """
    from sqlalchemy import select, and_
    try:
        stmt = select(IntegrationSyncJob).where(
            and_(
                IntegrationSyncJob.team_id == current_user.team_id,
                IntegrationSyncJob.integration_type == "github"
            )
        )

        if status:
            stmt = stmt.where(IntegrationSyncJob.status == status)

        stmt = stmt.order_by(IntegrationSyncJob.created_at.desc()).limit(limit)

        result = await db.execute(stmt)
        jobs = result.scalars().all()

        return {
            "jobs": [
                SyncJobResponse(
                    id=job.id,
                    status=job.status,
                    integration_type=job.integration_type,
                    sync_type=job.sync_type,
                    target_resource=job.target_resource or "",
                    total_items=job.total_items or 0,
                    processed_items=job.processed_items or 0,
                    failed_items=job.failed_items or 0,
                    items_created=job.items_created or 0,
                    items_updated=job.items_updated or 0,
                    started_at=job.started_at.isoformat() if job.started_at else None,
                    completed_at=job.completed_at.isoformat() if job.completed_at else None,
                    error_message=job.error_message
                )
                for job in jobs
            ],
            "total": len(jobs)
        }

    except Exception as e:
        logger.error("list_sync_jobs_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to list sync jobs: {str(e)}")


@router.post("/generate-embeddings")
async def generate_embeddings_for_indexed_files(
    background_tasks: BackgroundTasks,
    limit: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate embeddings for already-indexed files that don't have embeddings yet

    Useful if indexing was done without embeddings
    """
    from sqlalchemy import select, and_, func
    try:
        # Count unembedded files
        stmt = select(func.count()).select_from(ExternalContent).where(
            and_(
                ExternalContent.team_id == current_user.team_id,
                ExternalContent.source_type == "github",
                ExternalContent.vector_id == None,
                ExternalContent.is_deleted == False
            )
        )
        result = await db.execute(stmt)
        count = result.scalar()

        if count == 0:
            return {
                "message": "All indexed files already have embeddings",
                "files_to_process": 0
            }

        # Add background task
        async def generate_embeddings_task():
            try:
                embedding_service = get_github_code_embedding_service(db)
                stats = await embedding_service.batch_embed_github_contents(
                    team_id=current_user.team_id,
                    limit=limit
                )
                logger.info("batch_embedding_task_completed", stats=stats)
            except Exception as e:
                logger.error("batch_embedding_task_failed", error=str(e))

        background_tasks.add_task(generate_embeddings_task)

        logger.info(
            "embedding_generation_started",
            team_id=current_user.team_id,
            files_to_process=min(count, limit) if limit else count
        )

        return {
            "message": "Started generating embeddings",
            "files_to_process": min(count, limit) if limit else count
        }

    except Exception as e:
        logger.error("generate_embeddings_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to start embedding generation: {str(e)}")
