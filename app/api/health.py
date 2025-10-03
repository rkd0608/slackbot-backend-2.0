"""Health check and status endpoints"""
from typing import Dict, Any
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
from app.core.cache import cache_manager
from app.core.vector_db import vector_db_manager
from app.core.storage import storage_manager
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check() -> Dict[str, str]:
    """Basic health check endpoint"""
    return {"status": "healthy"}


@router.get("/health/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Readiness check - verify all dependencies are available"""
    checks = {
        "mysql": False,
        "redis": False,
        "pinecone": False,
        "s3": False
    }

    # Check MySQL
    try:
        await db.execute(text("SELECT 1"))
        checks["mysql"] = True
    except Exception as e:
        logger.error("mysql_health_check_failed", error=str(e))

    # Check Redis
    try:
        await cache_manager.client.ping()
        checks["redis"] = True
    except Exception as e:
        logger.error("redis_health_check_failed", error=str(e))

    # Check Pinecone
    try:
        stats = vector_db_manager.get_stats()
        checks["pinecone"] = bool(stats)
    except Exception as e:
        logger.error("pinecone_health_check_failed", error=str(e))

    # Check S3
    try:
        storage_manager.s3_client.head_bucket(Bucket=storage_manager.bucket)
        checks["s3"] = True
    except Exception as e:
        logger.error("s3_health_check_failed", error=str(e))

    all_healthy = all(checks.values())

    return {
        "status": "ready" if all_healthy else "not_ready",
        "checks": checks
    }


@router.get("/health/stats")
async def system_stats(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """System statistics endpoint"""
    stats = {}

    # Database stats
    try:
        result = await db.execute(text("SELECT COUNT(*) FROM messages"))
        stats["total_messages"] = result.scalar()

        result = await db.execute(text("SELECT COUNT(*) FROM channels"))
        stats["total_channels"] = result.scalar()

        result = await db.execute(text("SELECT COUNT(*) FROM users"))
        stats["total_users"] = result.scalar()
    except Exception as e:
        logger.error("database_stats_error", error=str(e))
        stats["database_error"] = str(e)

    # Vector DB stats
    try:
        vector_stats = vector_db_manager.get_stats()
        stats["vector_count"] = vector_stats.get("total_vector_count", 0)
    except Exception as e:
        logger.error("vector_stats_error", error=str(e))
        stats["vector_error"] = str(e)

    return stats
