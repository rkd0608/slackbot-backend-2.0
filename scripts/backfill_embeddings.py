"""Backfill embeddings for all messages and files"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import db_manager
from app.core.vector_db import vector_db_manager
from app.services.embedding_service import embedding_service
from app.core.logging import get_logger
from sqlalchemy import select, func
from app.models.message import Message
from app.models.file import File

logger = get_logger(__name__)


async def backfill_message_embeddings(batch_size: int = 100):
    """Backfill embeddings for all messages without vector_id"""

    print("\n" + "="*60)
    print("BACKFILL MESSAGE EMBEDDINGS")
    print("="*60)

    # Initialize services
    db_manager.initialize()
    vector_db_manager.initialize()

    async for session in db_manager.get_session():
        # Count total messages without embeddings
        result = await session.execute(
            select(func.count(Message.id)).where(Message.vector_id.is_(None))
        )
        total = result.scalar()

        print(f"\nTotal messages to embed: {total}")

        if total == 0:
            print("✓ All messages already have embeddings")
            return

        # Process in batches
        processed = 0
        failed = 0
        offset = 0

        while offset < total:
            # Get batch of messages
            result = await session.execute(
                select(Message)
                .where(Message.vector_id.is_(None))
                .limit(batch_size)
                .offset(offset)
            )
            messages = result.scalars().all()

            if not messages:
                break

            # Embed each message
            for message in messages:
                try:
                    success = await embedding_service.embed_message(
                        message_id=message.message_id,
                        db=session
                    )

                    if success:
                        processed += 1
                        if processed % 10 == 0:
                            print(f"  Progress: {processed}/{total} ({processed*100//total}%)")
                    else:
                        failed += 1

                except Exception as e:
                    logger.error("embed_error", message_id=message.message_id, error=str(e))
                    failed += 1

            offset += batch_size

            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)

        print(f"\n✓ Embedded {processed} messages")
        if failed > 0:
            print(f"✗ Failed to embed {failed} messages")

    await db_manager.close()


async def backfill_file_embeddings(batch_size: int = 50):
    """Backfill embeddings for all files without vector_id"""

    print("\n" + "="*60)
    print("BACKFILL FILE EMBEDDINGS")
    print("="*60)

    async for session in db_manager.get_session():
        # Count total files without embeddings
        result = await session.execute(
            select(func.count(File.id))
            .where(File.vector_id.is_(None))
            .where(File.extracted_text.isnot(None))
        )
        total = result.scalar()

        print(f"\nTotal files to embed: {total}")

        if total == 0:
            print("✓ All files already have embeddings")
            return

        # Process in batches
        processed = 0
        failed = 0
        offset = 0

        while offset < total:
            # Get batch of files
            result = await session.execute(
                select(File)
                .where(File.vector_id.is_(None))
                .where(File.extracted_text.isnot(None))
                .limit(batch_size)
                .offset(offset)
            )
            files = result.scalars().all()

            if not files:
                break

            # Embed each file
            for file in files:
                try:
                    success = await embedding_service.embed_file(
                        file_id=file.file_id,
                        db=session
                    )

                    if success:
                        processed += 1
                        if processed % 5 == 0:
                            print(f"  Progress: {processed}/{total} ({processed*100//total}%)")
                    else:
                        failed += 1

                except Exception as e:
                    logger.error("embed_error", file_id=file.file_id, error=str(e))
                    failed += 1

            offset += batch_size

            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)

        print(f"\n✓ Embedded {processed} files")
        if failed > 0:
            print(f"✗ Failed to embed {failed} files")

    await db_manager.close()


async def main():
    """Run backfill for both messages and files"""

    print("\n" + "="*60)
    print("BACKFILL ALL EMBEDDINGS")
    print("="*60)
    print("\nThis will embed all messages and files that don't have embeddings.")
    print("This may take several minutes depending on the number of items.")
    print("="*60)

    confirm = input("\nContinue? (y/N): ")
    if confirm.lower() != 'y':
        print("Cancelled.")
        return

    # Backfill messages
    await backfill_message_embeddings(batch_size=100)

    # Backfill files
    await backfill_file_embeddings(batch_size=50)

    print("\n" + "="*60)
    print("✓ BACKFILL COMPLETE")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
