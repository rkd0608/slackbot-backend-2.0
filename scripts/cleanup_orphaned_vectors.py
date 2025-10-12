"""Cleanup orphaned vectors in Pinecone that don't exist in MySQL"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import db_manager
from app.core.vector_db import vector_db_manager
from app.core.logging import get_logger
from sqlalchemy import select, text
from app.models.message import Message
from app.models.file import File

logger = get_logger(__name__)


async def get_valid_vector_ids():
    """Get all valid vector_ids from MySQL database"""

    print("Fetching valid vector IDs from MySQL...")

    valid_vector_ids = set()

    async for session in db_manager.get_session():
        # Get all message vector_ids
        result = await session.execute(
            text('SELECT vector_id FROM messages WHERE vector_id IS NOT NULL')
        )
        message_vector_ids = [row[0] for row in result.fetchall()]
        valid_vector_ids.update(message_vector_ids)

        # Get all file vector_ids
        result = await session.execute(
            text('SELECT vector_id FROM files WHERE vector_id IS NOT NULL')
        )
        file_vector_ids = [row[0] for row in result.fetchall()]
        valid_vector_ids.update(file_vector_ids)

        break  # Only use first session

    print(f"Found {len(valid_vector_ids)} valid vector IDs in MySQL")

    return valid_vector_ids


async def cleanup_orphaned_vectors(batch_size: int = 100, dry_run: bool = False):
    """
    Delete vectors from Pinecone that don't have corresponding records in MySQL

    Args:
        batch_size: Number of vectors to delete per batch
        dry_run: If True, only report what would be deleted without actually deleting
    """

    print("\n" + "="*60)
    print("CLEANUP ORPHANED VECTORS")
    print("="*60)

    if dry_run:
        print("\n🔍 DRY RUN MODE - No vectors will be deleted")
    else:
        print("\n⚠️  WARNING: This will DELETE orphaned vectors from Pinecone!")
        confirm = input("\nContinue? (yes/no): ")
        if confirm.lower() != 'yes':
            print("Cancelled.")
            return

    # Initialize services
    db_manager.initialize()
    vector_db_manager.initialize()

    # Get valid vector IDs from MySQL
    valid_vector_ids = await get_valid_vector_ids()

    # Get all vector IDs from Pinecone
    print("\nFetching all vectors from Pinecone...")

    # Pinecone doesn't have a direct "list all IDs" API, so we need to query
    # We'll fetch vectors in chunks using describe_index_stats and query

    index_stats = vector_db_manager.index.describe_index_stats()
    total_vectors_in_pinecone = index_stats.total_vector_count

    print(f"Total vectors in Pinecone: {total_vectors_in_pinecone}")
    print(f"Valid vectors in MySQL: {len(valid_vector_ids)}")
    print(f"Expected orphaned vectors: {total_vectors_in_pinecone - len(valid_vector_ids)}")

    # Since Pinecone doesn't have a list() method, we'll use a different approach:
    # We know the valid vector_ids from MySQL, so we can simply delete by checking
    # which ones exist in Pinecone but not in MySQL

    print("\nIdentifying orphaned vectors...")

    # We'll query for all message_ids and file_ids in Pinecone to find orphans
    all_pinecone_ids = set()

    try:
        # Strategy: Query for vectors and collect their IDs
        # We'll do multiple queries with different filters to cover all vectors

        # Query for all message vectors (type not set or not equal to "file")
        print("  Querying message vectors...")
        results = vector_db_manager.index.query(
            vector=[0.1] * 1536,
            top_k=10000,  # Maximum allowed
            include_metadata=False
        )
        message_ids = {match.id for match in results.matches}
        all_pinecone_ids.update(message_ids)
        print(f"    Found {len(message_ids)} message vectors")

        # Query for file vectors
        print("  Querying file vectors...")
        results = vector_db_manager.index.query(
            vector=[0.1] * 1536,
            filter={"type": "file"},
            top_k=10000,
            include_metadata=False
        )
        file_ids = {match.id for match in results.matches}
        all_pinecone_ids.update(file_ids)
        print(f"    Found {len(file_ids)} file vectors")

        print(f"  Total vectors found: {len(all_pinecone_ids)}")

        # Note: If we have more than 10k vectors, this won't get them all
        # But for now, with ~1k vectors, this approach works

    except Exception as e:
        logger.error("pinecone_query_error", error=str(e))
        print(f"\n❌ Error querying Pinecone: {e}")
        return

    # Find orphaned vectors (in Pinecone but not in MySQL)
    orphaned_vector_ids = all_pinecone_ids - valid_vector_ids

    print(f"\n📊 Analysis:")
    print(f"  Vectors in Pinecone: {len(all_pinecone_ids)}")
    print(f"  Valid vectors (in MySQL): {len(valid_vector_ids)}")
    print(f"  Orphaned vectors to delete: {len(orphaned_vector_ids)}")

    if len(orphaned_vector_ids) == 0:
        print("\n✅ No orphaned vectors found!")
        return

    if dry_run:
        print(f"\n🔍 DRY RUN: Would delete {len(orphaned_vector_ids)} orphaned vectors")
        print("\nSample orphaned vector IDs (first 10):")
        for vid in list(orphaned_vector_ids)[:10]:
            print(f"  - {vid}")
        print("\nRun without --dry-run to actually delete these vectors.")
        return

    # Delete orphaned vectors in batches
    print(f"\n🗑️  Deleting {len(orphaned_vector_ids)} orphaned vectors...")

    orphaned_list = list(orphaned_vector_ids)
    deleted_count = 0
    failed_count = 0

    for i in range(0, len(orphaned_list), batch_size):
        batch = orphaned_list[i:i + batch_size]

        try:
            # Delete batch from Pinecone
            vector_db_manager.index.delete(ids=batch)
            deleted_count += len(batch)

            if deleted_count % 100 == 0 or deleted_count == len(orphaned_list):
                print(f"  Progress: {deleted_count}/{len(orphaned_list)} ({deleted_count*100//len(orphaned_list)}%)")

        except Exception as e:
            logger.error("delete_batch_error", error=str(e), batch_size=len(batch))
            failed_count += len(batch)
            print(f"  ❌ Failed to delete batch: {e}")

        # Small delay to avoid rate limits
        await asyncio.sleep(0.1)

    print(f"\n✅ Cleanup complete!")
    print(f"  Deleted: {deleted_count} vectors")
    if failed_count > 0:
        print(f"  Failed: {failed_count} vectors")

    # Verify final count
    final_stats = vector_db_manager.index.describe_index_stats()
    print(f"\n📊 Final Pinecone stats:")
    print(f"  Total vectors: {final_stats.total_vector_count}")
    print(f"  Expected: {len(valid_vector_ids)}")

    await db_manager.close()


async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Cleanup orphaned vectors in Pinecone')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted without actually deleting')
    parser.add_argument('--batch-size', type=int, default=100, help='Batch size for deletion (default: 100)')

    args = parser.parse_args()

    await cleanup_orphaned_vectors(batch_size=args.batch_size, dry_run=args.dry_run)

    print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
