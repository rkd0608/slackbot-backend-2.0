"""Service for retrying failed file processing"""
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from app.core.database import db_manager
from app.core.logging import get_logger
from app.models.file import File
from app.services.slack_client import slack_client_manager

logger = get_logger(__name__)


class FileRecoveryService:
    """Manages retry of failed file processing"""

    async def retry_failed_files(self):
        """
        Retry files that failed to download or process

        Uses exponential backoff:
        - Retry 1: 1 minute
        - Retry 2: 5 minutes
        - Retry 3: 15 minutes
        - Retry 4: 1 hour
        - Retry 5: 4 hours
        """
        try:
            async for db in db_manager.get_session():
                # Get files ready for retry (failed and backoff period elapsed)
                now = datetime.utcnow()

                stmt = (
                    select(File)
                    .where(File.is_processed == 0)  # Not successfully processed
                    .where(File.retry_count < File.max_retries)  # Haven't exceeded max retries
                    .where(
                        and_(
                            File.processing_error.isnot(None),  # Has an error
                            (File.next_retry_at.is_(None)) | (File.next_retry_at <= now)  # Ready for retry
                        )
                    )
                    .order_by(File.created_at)
                    .limit(50)  # Process 50 at a time
                )

                result = await db.execute(stmt)
                failed_files = result.scalars().all()

                if not failed_files:
                    logger.debug("no_failed_files_to_retry")
                    return {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}

                processed = 0
                succeeded = 0
                failed = 0
                skipped = 0

                for file in failed_files:
                    processed += 1

                    # Calculate next retry time using exponential backoff
                    backoff_minutes = self._calculate_backoff(file.retry_count)
                    next_retry = now + timedelta(minutes=backoff_minutes)

                    try:
                        # Import here to avoid circular imports
                        from app.services.file_processor import file_processor
                        from app.models.workspace import Workspace
                        from slack_sdk import WebClient
                        from slack_sdk.errors import SlackApiError

                        logger.info(
                            "retrying_failed_file",
                            file_id=file.file_id,
                            filename=file.filename,
                            retry_count=file.retry_count,
                            previous_error=file.processing_error
                        )

                        # Get workspace to use its bot token (CRITICAL for multi-tenancy)
                        workspace_result = await db.execute(
                            select(Workspace).where(Workspace.team_id == file.team_id)
                        )
                        workspace = workspace_result.scalar_one_or_none()

                        if not workspace or not workspace.bot_access_token:
                            logger.error(
                                "workspace_not_found_for_file_retry",
                                file_id=file.file_id,
                                team_id=file.team_id
                            )
                            file.retry_count += 1
                            file.last_retry_at = now
                            file.next_retry_at = next_retry
                            file.processing_error = "Workspace or bot token not found"
                            skipped += 1
                            continue

                        # Create workspace-specific Slack client
                        workspace_client = WebClient(token=workspace.bot_access_token)

                        # Fetch fresh file_info from Slack using workspace token
                        file_info = None
                        try:
                            response = workspace_client.files_info(file=file.file_id)
                            file_info = response["file"]

                            logger.info(
                                "fetched_fresh_file_info_for_retry",
                                file_id=file.file_id,
                                url_private_download=file_info.get("url_private_download", "")[:100],
                                url_private=file_info.get("url_private", "")[:100],
                                permalink_public=file_info.get("permalink_public", "")[:100]
                            )

                            # Try to generate public URL
                            try:
                                public_response = workspace_client.files_sharedPublicURL(file=file.file_id)
                                if public_response["ok"]:
                                    file_info["permalink_public"] = public_response["file"].get("permalink_public")
                                    logger.info(
                                        "generated_public_url_for_retry",
                                        file_id=file.file_id,
                                        has_public_url=bool(public_response["file"].get("permalink_public"))
                                    )
                            except SlackApiError as e:
                                logger.debug("public_url_not_available_for_retry", file_id=file.file_id, error=str(e))
                        except SlackApiError as e:
                            logger.error("file_info_fetch_failed_for_retry", file_id=file.file_id, error=str(e))

                        if not file_info:
                            # If we can't fetch from Slack, skip this file
                            logger.error(
                                "cannot_retry_without_slack_file_info",
                                file_id=file.file_id
                            )
                            file.retry_count += 1
                            file.last_retry_at = now
                            file.next_retry_at = next_retry
                            file.processing_error = "Cannot fetch file info from Slack API"
                            skipped += 1
                            continue

                        # Retry processing (SECURITY: Use file's team_id and workspace token)
                        result_file = await file_processor.process_file(
                            file_info=file_info,
                            channel_id=file.channel_id,
                            user_id=file.user_id,
                            team_id=file.team_id,  # Use file's team_id (secure, no Channel lookup needed)
                            db=db,
                            message_id=file.message_id,
                            bot_token=workspace.bot_access_token  # CRITICAL: Use workspace token for download
                        )

                        if result_file and result_file.is_processed:
                            # Success!
                            succeeded += 1
                            logger.info(
                                "file_retry_succeeded",
                                file_id=file.file_id,
                                filename=file.filename,
                                retry_count=file.retry_count
                            )
                        else:
                            # Still failed - increment retry count
                            file.retry_count += 1
                            file.last_retry_at = now
                            file.next_retry_at = next_retry

                            if file.retry_count >= file.max_retries:
                                # Max retries reached - give up
                                file.processing_error = f"Max retries ({file.max_retries}) exceeded. Last error: {file.processing_error}"
                                failed += 1
                                logger.error(
                                    "file_max_retries_exceeded",
                                    file_id=file.file_id,
                                    filename=file.filename,
                                    retry_count=file.retry_count
                                )
                            else:
                                skipped += 1
                                logger.warning(
                                    "file_retry_failed_will_retry",
                                    file_id=file.file_id,
                                    filename=file.filename,
                                    retry_count=file.retry_count,
                                    next_retry_at=next_retry.isoformat()
                                )

                    except Exception as e:
                        # Exception during retry - increment count and schedule next retry
                        file.retry_count += 1
                        file.last_retry_at = now
                        file.next_retry_at = next_retry
                        file.processing_error = f"Retry {file.retry_count} failed: {str(e)}"

                        if file.retry_count >= file.max_retries:
                            failed += 1
                            logger.error(
                                "file_max_retries_exception",
                                file_id=file.file_id,
                                filename=file.filename,
                                retry_count=file.retry_count,
                                error=str(e)
                            )
                        else:
                            skipped += 1
                            logger.warning(
                                "file_retry_exception_will_retry",
                                file_id=file.file_id,
                                filename=file.filename,
                                retry_count=file.retry_count,
                                next_retry_at=next_retry.isoformat(),
                                error=str(e)
                            )

                # Commit all changes
                await db.commit()

                logger.info(
                    "failed_files_retry_complete",
                    processed=processed,
                    succeeded=succeeded,
                    failed=failed,
                    skipped=skipped
                )

                return {
                    "processed": processed,
                    "succeeded": succeeded,
                    "failed": failed,
                    "skipped": skipped
                }

        except Exception as e:
            logger.error("retry_failed_files_error", error=str(e))
            return {"error": str(e)}

    def _calculate_backoff(self, retry_count: int) -> int:
        """
        Calculate exponential backoff in minutes

        Retry 1: 1 minute
        Retry 2: 5 minutes
        Retry 3: 15 minutes
        Retry 4: 60 minutes (1 hour)
        Retry 5: 240 minutes (4 hours)
        """
        backoff_schedule = [1, 5, 15, 60, 240]
        if retry_count < len(backoff_schedule):
            return backoff_schedule[retry_count]
        return backoff_schedule[-1]  # Cap at max

    async def _get_team_id_for_channel(self, channel_id: str, db) -> str:
        """Get team_id for a given channel"""
        from app.models.channel import Channel
        from sqlalchemy import select

        stmt = select(Channel.team_id).where(Channel.channel_id == channel_id)
        result = await db.execute(stmt)
        team_id = result.scalar_one_or_none()
        return team_id or "unknown"

    async def get_failed_files_stats(self) -> dict:
        """Get statistics about failed files"""
        try:
            async for db in db_manager.get_session():
                # Files with errors
                failed_stmt = select(File).where(
                    and_(
                        File.is_processed == 0,
                        File.processing_error.isnot(None)
                    )
                )

                # Files that can be retried
                retriable_stmt = select(File).where(
                    and_(
                        File.is_processed == 0,
                        File.processing_error.isnot(None),
                        File.retry_count < File.max_retries
                    )
                )

                # Files that exceeded max retries
                exhausted_stmt = select(File).where(
                    and_(
                        File.is_processed == 0,
                        File.processing_error.isnot(None),
                        File.retry_count >= File.max_retries
                    )
                )

                failed_result = await db.execute(failed_stmt)
                retriable_result = await db.execute(retriable_stmt)
                exhausted_result = await db.execute(exhausted_stmt)

                failed_count = len(failed_result.scalars().all())
                retriable_count = len(retriable_result.scalars().all())
                exhausted_count = len(exhausted_result.scalars().all())

                return {
                    "total_failed": failed_count,
                    "retriable": retriable_count,
                    "exhausted": exhausted_count
                }

        except Exception as e:
            logger.error("get_failed_files_stats_error", error=str(e))
            return {"error": str(e)}


# Global instance
file_recovery_service = FileRecoveryService()
