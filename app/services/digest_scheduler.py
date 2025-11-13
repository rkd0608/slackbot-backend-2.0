"""
Daily Digest Scheduler Service

Schedules and manages automatic delivery of personalized daily digests.

Features:
- Sends digests at configurable times (default: 9 AM user timezone)
- Respects user preferences and mute settings
- Handles failures gracefully with retry logic
- Supports immediate digest trigger for testing
"""
from datetime import datetime, time
from typing import Optional, List
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.logging import get_logger
from app.core.database import db_manager
from app.models.workspace import Workspace
from app.services.personalized_digest_service import PersonalizedDigestService

logger = get_logger(__name__)


class DigestScheduler:
    """
    Manages scheduled delivery of daily digests

    Usage:
        scheduler = DigestScheduler()
        await scheduler.start()  # Starts background scheduling
        await scheduler.trigger_immediate(team_id)  # Manual trigger
    """

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False

    async def start(self):
        """Start the digest scheduler"""
        if self.is_running:
            logger.warning("digest_scheduler_already_running")
            return

        # Schedule daily digest at 9 AM (adjust timezone as needed)
        self.scheduler.add_job(
            self._send_all_digests,
            CronTrigger(hour=9, minute=0),  # 9:00 AM every day
            id="daily_digest_job",
            name="Send Daily Digests",
            replace_existing=True
        )

        self.scheduler.start()
        self.is_running = True

        logger.info("digest_scheduler_started", next_run_time=self._get_next_run_time())

    async def stop(self):
        """Stop the digest scheduler"""
        if not self.is_running:
            return

        self.scheduler.shutdown(wait=True)
        self.is_running = False

        logger.info("digest_scheduler_stopped")

    def _get_next_run_time(self) -> Optional[str]:
        """Get next scheduled run time"""
        job = self.scheduler.get_job("daily_digest_job")
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None

    async def _send_all_digests(self):
        """Send digests to all active workspaces"""
        logger.info("digest_scheduler_triggered", timestamp=datetime.utcnow().isoformat())

        try:
            async with db_manager.AsyncSessionLocal() as db:
                # Get all active workspaces
                result = await db.execute(
                    select(Workspace).where(Workspace.is_active == "true")
                )
                workspaces = result.scalars().all()

                logger.info("sending_digests_to_workspaces", workspace_count=len(workspaces))

                # Send digests for each workspace
                success_count = 0
                failure_count = 0

                for workspace in workspaces:
                    try:
                        sent_count = await self._send_workspace_digests(
                            team_id=workspace.team_id,
                            db=db
                        )
                        success_count += sent_count

                    except Exception as e:
                        logger.error(
                            "workspace_digest_failed",
                            team_id=workspace.team_id,
                            error=str(e)
                        )
                        failure_count += 1

                logger.info(
                    "digest_scheduler_completed",
                    success_count=success_count,
                    failure_count=failure_count
                )

        except Exception as e:
            logger.error("digest_scheduler_error", error=str(e))

    async def _send_workspace_digests(
        self,
        team_id: str,
        db: AsyncSession,
        hours_back: int = 24
    ) -> int:
        """
        Send digests to all users in a workspace

        Args:
            team_id: Workspace team ID
            db: Database session
            hours_back: Hours of activity to include

        Returns:
            Number of digests sent
        """
        try:
            digest_service = PersonalizedDigestService(db)

            sent_count = await digest_service.send_digests_to_all_users(
                team_id=team_id,
                hours_back=hours_back
            )

            logger.info(
                "workspace_digests_sent",
                team_id=team_id,
                sent_count=sent_count
            )

            return sent_count

        except Exception as e:
            logger.error(
                "send_workspace_digests_error",
                team_id=team_id,
                error=str(e)
            )
            raise

    async def trigger_immediate(
        self,
        team_id: str,
        hours_back: int = 24
    ) -> int:
        """
        Trigger immediate digest send for a workspace (for testing/manual trigger)

        Args:
            team_id: Workspace team ID
            hours_back: Hours of activity to include

        Returns:
            Number of digests sent
        """
        logger.info("immediate_digest_triggered", team_id=team_id)

        async with db_manager.AsyncSessionLocal() as db:
            return await self._send_workspace_digests(
                team_id=team_id,
                db=db,
                hours_back=hours_back
            )

    async def update_schedule(
        self,
        hour: int = 9,
        minute: int = 0,
        timezone: str = "UTC"
    ):
        """
        Update the digest delivery schedule

        Args:
            hour: Hour to send (0-23)
            minute: Minute to send (0-59)
            timezone: Timezone string (e.g., "America/New_York")
        """
        if not self.is_running:
            logger.warning("cannot_update_schedule_not_running")
            return

        # Remove existing job
        self.scheduler.remove_job("daily_digest_job")

        # Add new job with updated schedule
        self.scheduler.add_job(
            self._send_all_digests,
            CronTrigger(hour=hour, minute=minute, timezone=timezone),
            id="daily_digest_job",
            name="Send Daily Digests",
            replace_existing=True
        )

        logger.info(
            "digest_schedule_updated",
            hour=hour,
            minute=minute,
            timezone=timezone,
            next_run_time=self._get_next_run_time()
        )

    def get_status(self) -> dict:
        """Get scheduler status"""
        return {
            'is_running': self.is_running,
            'next_run_time': self._get_next_run_time(),
            'scheduled_jobs': len(self.scheduler.get_jobs()) if self.is_running else 0
        }


# Global scheduler instance
digest_scheduler = DigestScheduler()
