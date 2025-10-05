"""Update messages with missing user names"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, update
from app.core.database import db_manager
from app.models.message import Message
from app.models.user import User
from app.services.sync_service import sync_service
from app.core.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


async def update_messages_with_usernames():
    """Update all messages that have NULL user_name"""

    db_manager.initialize()

    async with db_manager.AsyncSessionLocal() as db:
        try:
            # Get all messages with NULL user_name
            result = await db.execute(
                select(Message).where(Message.user_name.is_(None))
            )
            messages = result.scalars().all()

            logger.info(f"Found {len(messages)} messages with missing user names")

            if not messages:
                logger.info("No messages to update")
                return

            # Get unique user IDs
            user_ids = list(set([msg.user_id for msg in messages if msg.user_id]))
            logger.info(f"Found {len(user_ids)} unique users to sync")

            # Sync all users first
            user_map = {}
            for user_id in user_ids:
                # Check if user exists
                result = await db.execute(
                    select(User).where(User.user_id == user_id)
                )
                user = result.scalar_one_or_none()

                if not user:
                    # Sync from Slack
                    logger.info(f"Syncing user {user_id}")
                    user = await sync_service.sync_user(user_id, db)

                if user:
                    user_name = user.display_name or user.real_name or user.username
                    user_map[user_id] = user_name
                    logger.info(f"User {user_id} -> {user_name}")

            # Update messages
            updated = 0
            for message in messages:
                if message.user_id in user_map:
                    message.user_name = user_map[message.user_id]
                    updated += 1

                    if updated % 100 == 0:
                        logger.info(f"Updated {updated} messages...")
                        await db.commit()

            # Final commit
            await db.commit()

            logger.info(f"Successfully updated {updated} messages with user names")

        except Exception as e:
            logger.error(f"Error updating messages: {e}")
            await db.rollback()
            raise
        finally:
            await db_manager.close()


if __name__ == "__main__":
    asyncio.run(update_messages_with_usernames())
