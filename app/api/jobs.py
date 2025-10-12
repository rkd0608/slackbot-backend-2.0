"""Background job monitoring and control API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.core.scheduler import scheduler_manager
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class JobTriggerRequest(BaseModel):
    """Request to manually trigger a job"""
    job_id: str


@router.get("/jobs")
async def list_all_jobs():
    """Get status of all scheduled jobs"""

    try:
        jobs = scheduler_manager.get_all_jobs()

        return {
            "total_jobs": len(jobs),
            "jobs": jobs
        }

    except Exception as e:
        logger.error("list_jobs_error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list jobs")


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get status of a specific job"""

    try:
        job_status = scheduler_manager.get_job_status(job_id)

        if "error" in job_status:
            raise HTTPException(status_code=404, detail=job_status["error"])

        return job_status

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_job_status_error", error=str(e), job_id=job_id)
        raise HTTPException(status_code=500, detail="Failed to get job status")


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    """Pause a scheduled job"""

    try:
        result = scheduler_manager.pause_job(job_id)

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("pause_job_error", error=str(e), job_id=job_id)
        raise HTTPException(status_code=500, detail="Failed to pause job")


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    """Resume a paused job"""

    try:
        result = scheduler_manager.resume_job(job_id)

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("resume_job_error", error=str(e), job_id=job_id)
        raise HTTPException(status_code=500, detail="Failed to resume job")


@router.post("/jobs/trigger")
async def trigger_job(request: JobTriggerRequest):
    """Manually trigger a job to run immediately"""

    try:
        result = scheduler_manager.trigger_job(request.job_id)

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))

        logger.info("job_manually_triggered", job_id=request.job_id)

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("trigger_job_error", error=str(e), job_id=request.job_id)
        raise HTTPException(status_code=500, detail="Failed to trigger job")


@router.get("/jobs/health")
async def jobs_health_check():
    """Health check for background job system"""

    try:
        jobs = scheduler_manager.get_all_jobs()

        # Count jobs by status
        scheduled_jobs = [j for j in jobs if j.get("next_run")]

        health_status = {
            "status": "healthy",
            "scheduler_running": scheduler_manager.scheduler.running if scheduler_manager.scheduler else False,
            "total_jobs": len(jobs),
            "scheduled_jobs": len(scheduled_jobs),
            "jobs": jobs
        }

        return health_status

    except Exception as e:
        logger.error("jobs_health_check_error", error=str(e))
        return {
            "status": "unhealthy",
            "error": str(e)
        }
