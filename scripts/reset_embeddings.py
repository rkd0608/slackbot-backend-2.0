"""Reset all embeddings - clear Pinecone and reset vector_ids in database"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import db_manager
from app.core.vector_db import vector_db_manager
from app.core.logging import get_logger
from sqlalchemy import text

logger = get_logger(__name__)


async def reset_embeddings():
    """Clear all embeddings from Pinecone and reset vector_ids in database"""

    print("\n" + "="*60)
    print("RESET EMBEDDINGS")
    print("="*60)
    print("\nThis will:")
    print("1. Delete ALL vectors from Pinecone index")
    print("2. Set all vector_id = NULL in messages table")
    print("3. Set all vector_id = NULL in files table")
    print("\nYou will need to re-run backfill after this.")
    print("="*60)

    confirm = input("\nType 'RESET' to confirm: ")
    if confirm != "RESET":
        print("Cancelled.")
        return

    # Initialize services
    print("\n[1/4] Initializing services...")
    db_manager.initialize()
    vector_db_manager.initialize()

    # Clear Pinecone index
    print("\n[2/4] Deleting all vectors from Pinecone...")
    try:
        # Get index stats first
        stats = vector_db_manager.index.describe_index_stats()
        vector_count = stats.get('total_vector_count', 0)
        print(f"  Found {vector_count} vectors in Pinecone")

        # Delete all vectors
        vector_db_manager.index.delete(delete_all=True)
        print(f"  ✓ Deleted all vectors from Pinecone index: {settings.pinecone_index_name}")
    except Exception as e:
        print(f"  ✗ Error clearing Pinecone: {e}")
        return

    # Reset vector_ids in messages table
    print("\n[3/4] Resetting vector_id in messages table...")
    async for session in db_manager.get_session():
        try:
            result = await session.execute(
                text("UPDATE messages SET vector_id = NULL WHERE vector_id IS NOT NULL")
            )
            await session.commit()
            print(f"  ✓ Reset {result.rowcount} message vector_ids")
        except Exception as e:
            print(f"  ✗ Error resetting message vector_ids: {e}")
            await session.rollback()
            return

    # Reset vector_ids in files table
    print("\n[4/4] Resetting vector_id in files table...")
    async for session in db_manager.get_session():
        try:
            result = await session.execute(
                text("UPDATE files SET vector_id = NULL WHERE vector_id IS NOT NULL")
            )
            await session.commit()
            print(f"  ✓ Reset {result.rowcount} file vector_ids")
        except Exception as e:
            print(f"  ✗ Error resetting file vector_ids: {e}")
            await session.rollback()
            return

    # Close database
    await db_manager.close()

    print("\n" + "="*60)
    print("✓ RESET COMPLETE")
    print("="*60)
    print("\nNext steps:")
    print("1. Run backfill script to re-embed all messages and files")
    print("   docker-compose exec app python scripts/backfill_embeddings.py")
    print("\n")


if __name__ == "__main__":
    asyncio.run(reset_embeddings())
