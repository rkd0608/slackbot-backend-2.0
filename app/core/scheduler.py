"""Background job scheduler using APScheduler"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from datetime import datetime
from app.core.logging import get_logger
from app.core.config import settings

logger = get_logger(__name__)


class SchedulerManager:
    """Manages scheduled background jobs"""

    def __init__(self):
        self.scheduler = None
        self._initialized = False

    def initialize(self):
        """Initialize the scheduler with job stores and executors"""

        if self._initialized:
            logger.warning("scheduler_already_initialized")
            return

        jobstores = {
            'default': MemoryJobStore()
        }

        executors = {
            'default': AsyncIOExecutor()
        }

        job_defaults = {
            'coalesce': True,  # Combine multiple pending executions into one
            'max_instances': 1,  # Only one instance of each job at a time
            'misfire_grace_time': 300  # 5 minutes grace period for late jobs
        }

        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone='UTC'
        )

        # Add all scheduled jobs
        self._add_jobs()

        self._initialized = True
        logger.info("scheduler_initialized")

    def _add_jobs(self):
        """Add all scheduled jobs to the scheduler"""

        # Import workers here to avoid circular imports
        from app.workers.notification_worker import notification_worker
        from app.workers.initial_indexing import initial_indexing_worker
        from app.services.event_buffer_service import event_buffer_service

        # ====================
        # TRIAL MANAGEMENT
        # ====================

        # Check for trial expirations - Run daily at 9:00 AM UTC
        self.scheduler.add_job(
            func=notification_worker.check_trial_expirations,
            trigger=CronTrigger(hour=9, minute=0),
            id='trial_expiration_check',
            name='Check Trial Expirations',
            replace_existing=True
        )
        logger.info("scheduled_job_added", job_id="trial_expiration_check", schedule="Daily 9:00 AM UTC")

        # Check for expired trials - Run daily at 10:00 AM UTC
        self.scheduler.add_job(
            func=notification_worker.check_expired_trials,
            trigger=CronTrigger(hour=10, minute=0),
            id='expired_trial_check',
            name='Check Expired Trials',
            replace_existing=True
        )
        logger.info("scheduled_job_added", job_id="expired_trial_check", schedule="Daily 10:00 AM UTC")

        # ====================
        # USAGE & BILLING
        # ====================

        # Reset monthly query limits - Run on 1st of each month at midnight UTC
        self.scheduler.add_job(
            func=notification_worker.reset_monthly_query_limits,
            trigger=CronTrigger(day=1, hour=0, minute=0),
            id='reset_monthly_queries',
            name='Reset Monthly Query Limits',
            replace_existing=True
        )
        logger.info("scheduled_job_added", job_id="reset_monthly_queries", schedule="1st of month, midnight UTC")

        # ====================
        # USER COUNT SYNC
        # ====================

        # Sync user counts from Slack - Run daily at 2:00 AM UTC
        self.scheduler.add_job(
            func=notification_worker.sync_workspace_user_counts,
            trigger=CronTrigger(hour=2, minute=0),
            id='sync_user_counts',
            name='Sync Workspace User Counts',
            replace_existing=True
        )
        logger.info("scheduled_job_added", job_id="sync_user_counts", schedule="Daily 2:00 AM UTC")

        # ====================
        # HEALTH CHECKS
        # ====================

        # Scheduler health check - Run every 5 minutes
        self.scheduler.add_job(
            func=self._scheduler_health_check,
            trigger=IntervalTrigger(minutes=5),
            id='scheduler_health_check',
            name='Scheduler Health Check',
            replace_existing=True
        )
        logger.info("scheduled_job_added", job_id="scheduler_health_check", schedule="Every 5 minutes")

        # ====================
        # EVENT RELIABILITY
        # ====================

        # Retry buffered events - Run every 30 seconds
        self.scheduler.add_job(
            func=event_buffer_service.retry_buffered_events,
            trigger=IntervalTrigger(seconds=30),
            id='retry_buffered_events',
            name='Retry Buffered Events',
            replace_existing=True
        )
        logger.info("scheduled_job_added", job_id="retry_buffered_events", schedule="Every 30 seconds")

        # Clean up old processed events - Run daily at 4:00 AM UTC
        self.scheduler.add_job(
            func=event_buffer_service.cleanup_old_processed_events,
            trigger=CronTrigger(hour=4, minute=0),
            id='cleanup_processed_events',
            name='Cleanup Old Processed Events',
            replace_existing=True
        )
        logger.info("scheduled_job_added", job_id="cleanup_processed_events", schedule="Daily 4:00 AM UTC")

        # ====================
        # CLEANUP JOBS
        # ====================

        # Clean up old installation logs - Run weekly on Sunday at 3:00 AM UTC
        self.scheduler.add_job(
            func=self._cleanup_old_logs,
            trigger=CronTrigger(day_of_week='sun', hour=3, minute=0),
            id='cleanup_old_logs',
            name='Cleanup Old Installation Logs',
            replace_existing=True
        )
        logger.info("scheduled_job_added", job_id="cleanup_old_logs", schedule="Weekly Sunday 3:00 AM UTC")

    async def _scheduler_health_check(self):
        """Log scheduler health status"""
        running_jobs = len(self.scheduler.get_jobs())
        logger.info(
            "scheduler_health_check",
            status="healthy",
            running_jobs=running_jobs,
            timestamp=datetime.utcnow().isoformat()
        )

    async def _cleanup_old_logs(self):
        """Clean up installation logs older than 90 days"""
        try:
            from datetime import timedelta
            from app.core.database import db_manager
            from app.models.workspace import InstallationLog
            from sqlalchemy import delete

            cutoff_date = datetime.utcnow() - timedelta(days=90)

            async with db_manager.get_session_context() as db:
                # Delete logs older than 90 days
                stmt = delete(InstallationLog).where(
                    InstallationLog.created_at < cutoff_date
                )
                result = await db.execute(stmt)
                await db.commit()

                deleted_count = result.rowcount

                logger.info(
                    "old_logs_cleaned",
                    deleted_count=deleted_count,
                    cutoff_date=cutoff_date.isoformat()
                )

        except Exception as e:
            logger.error("cleanup_old_logs_error", error=str(e))

    def start(self):
        """Start the scheduler"""
        if not self._initialized:
            logger.error("scheduler_not_initialized")
            raise RuntimeError("Scheduler not initialized. Call initialize() first.")

        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("scheduler_started", jobs=len(self.scheduler.get_jobs()))
        else:
            logger.warning("scheduler_already_running")

    def shutdown(self):
        """Shutdown the scheduler gracefully"""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            logger.info("scheduler_shutdown")

    def get_job_status(self, job_id: str) -> dict:
        """Get status of a specific job"""
        job = self.scheduler.get_job(job_id)
        if not job:
            return {"error": "Job not found"}

        return {
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger)
        }

    def get_all_jobs(self) -> list:
        """Get status of all scheduled jobs"""
        jobs = self.scheduler.get_jobs()
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger)
            }
            for job in jobs
        ]

    def pause_job(self, job_id: str):
        """Pause a specific job"""
        try:
            self.scheduler.pause_job(job_id)
            logger.info("job_paused", job_id=job_id)
            return {"success": True, "job_id": job_id, "status": "paused"}
        except Exception as e:
            logger.error("pause_job_error", job_id=job_id, error=str(e))
            return {"success": False, "error": str(e)}

    def resume_job(self, job_id: str):
        """Resume a paused job"""
        try:
            self.scheduler.resume_job(job_id)
            logger.info("job_resumed", job_id=job_id)
            return {"success": True, "job_id": job_id, "status": "resumed"}
        except Exception as e:
            logger.error("resume_job_error", job_id=job_id, error=str(e))
            return {"success": False, "error": str(e)}

    def trigger_job(self, job_id: str):
        """Manually trigger a job to run immediately"""
        try:
            job = self.scheduler.get_job(job_id)
            if not job:
                return {"success": False, "error": "Job not found"}

            job.modify(next_run_time=datetime.utcnow())
            logger.info("job_triggered", job_id=job_id)
            return {"success": True, "job_id": job_id, "status": "triggered"}
        except Exception as e:
            logger.error("trigger_job_error", job_id=job_id, error=str(e))
            return {"success": False, "error": str(e)}


# Global scheduler instance
scheduler_manager = SchedulerManager()
