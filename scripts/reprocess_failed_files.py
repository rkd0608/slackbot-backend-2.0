"""Reprocess files that failed to download"""
import asyncio
import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.core.database import db_manager
from app.core.queue import queue_manager
from app.models.file import File
from app.models.message import Message
from app.core.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


async def reprocess_failed_files():
    """Reprocess files that failed to download"""

    db_manager.initialize()
    queue_manager.initialize()

    async with db_manager.AsyncSessionLocal() as db:
        try:
            # Get all files that failed to process
            result = await db.execute(
                select(File).where(
                    (File.is_processed == 0) | (File.processing_error.isnot(None))
                )
            )
            files = result.scalars().all()

            logger.info(f"Found {len(files)} files to reprocess")

            if not files:
                logger.info("No files to reprocess")
                return

            # For each file, get the original message and requeue
            requeued = 0
            for file_record in files:
                # Get the message that contains this file
                msg_result = await db.execute(
                    select(Message).where(
                        Message.file_ids.like(f'%{file_record.file_id}%')
                    )
                )
                message = msg_result.scalar_one_or_none()

                if not message:
                    logger.warning(f"No message found for file {file_record.file_id}")
                    continue

                # Parse raw_json to get file info
                try:
                    # raw_json is already a dict (stored as JSON type in MySQL)
                    raw_data = message.raw_json if message.raw_json else {}
                    files_data = raw_data.get("files", [])

                    # Find matching file
                    file_info = None
                    for f in files_data:
                        if f.get("id") == file_record.file_id:
                            file_info = f
                            break

                    if not file_info:
                        logger.warning(f"File info not found in message for {file_record.file_id}")
                        continue

                    # Queue for reprocessing
                    queue_manager.publish(
                        queue=queue_manager.PROCESSING_QUEUE,
                        message={
                            "type": "file_processing",
                            "file_id": file_record.file_id,
                            "file_info": file_info,
                            "channel_id": message.channel_id,
                            "user_id": message.user_id,
                            "team_id": message.team_id,
                            "message_id": message.message_id
                        }
                    )

                    logger.info(f"Requeued file {file_record.file_id} ({file_record.filename})")
                    requeued += 1

                except Exception as e:
                    logger.error(f"Error reprocessing file {file_record.file_id}: {e}")

            logger.info(f"Successfully requeued {requeued} files for processing")

        except Exception as e:
            logger.error(f"Error reprocessing files: {e}")
            raise
        finally:
            queue_manager.close()
            await db_manager.close()


if __name__ == "__main__":
    asyncio.run(reprocess_failed_files())
