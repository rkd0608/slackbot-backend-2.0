"""Background worker for historical data backfill"""
import asyncio
from typing import Optional
from app.services.backfill_service import backfill_service
from app.services.sync_service import sync_service
from app.core.database import db_manager
from app.core.logging import get_logger

logger = get_logger(__name__)


class BackfillWorker:
    """Worker for running historical data backfill jobs"""

    def __init__(self):
        self.running = False

    async def run_full_backfill(
        self,
        limit_per_channel: Optional[int] = 1000
    ):
        """Run complete backfill process"""

        logger.info("full_backfill_started", limit_per_channel=limit_per_channel)
        self.running = True

        async with db_manager.AsyncSessionLocal() as db:
            try:
                # Step 1: Sync all channels
                logger.info("syncing_channels")
                channel_count = await sync_service.sync_all_channels(db)
                logger.info("channels_synced", count=channel_count)

                # Step 2: Backfill messages from all channels
                logger.info("backfilling_messages")
                results = await backfill_service.backfill_all_channels(
                    db,
                    limit_per_channel=limit_per_channel
                )

                total_messages = sum(results.values())
                logger.info("backfill_messages_completed", total=total_messages, channels=len(results))

                # Step 3: Sync users
                logger.info("syncing_users")
                user_count = await sync_service.sync_all_users(db)
                logger.info("users_synced", count=user_count)

                logger.info(
                    "full_backfill_completed",
                    channels=channel_count,
                    messages=total_messages,
                    users=user_count
                )

                self.running = False
                return {
                    "channels": channel_count,
                    "messages": total_messages,
                    "users": user_count
                }

            except Exception as e:
                logger.error("full_backfill_error", error=str(e))
                self.running = False
                raise

    async def resume_backfill(self):
        """Resume interrupted backfill from checkpoints"""

        logger.info("resuming_backfill")
        self.running = True

        async with db_manager.AsyncSessionLocal() as db:
            try:
                results = await backfill_service.resume_backfill(db)
                total = sum(results.values())

                logger.info("resume_backfill_completed", total=total, channels=len(results))
                self.running = False

                return results

            except Exception as e:
                logger.error("resume_backfill_error", error=str(e))
                self.running = False
                raise


# Global backfill worker instance
backfill_worker = BackfillWorker()


# Main entry point for running as standalone process
async def main():
    """Run backfill worker as standalone process"""
    db_manager.initialize()

    try:
        await backfill_worker.run_full_backfill(limit_per_channel=1000)
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
