#!/usr/bin/env python3
"""
Backfill team_id for Files Table

SECURITY CRITICAL: This script backfills team_id for existing file records
to ensure proper multi-tenancy isolation.

Usage:
    python scripts/backfill_files_team_id.py

This script:
1. Finds all files without team_id
2. Looks up team_id from the associated channel
3. Updates the file with the correct team_id
4. Reports progress and statistics

Run this AFTER the migration 20250131_add_team_id_to_files if needed.
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import db_manager
from app.core.logging import get_logger, setup_logging
from app.models.file import File
from app.models.channel import Channel

# Setup logging
setup_logging()
logger = get_logger(__name__)


async def backfill_files_team_id():
    """Backfill team_id for all files missing it"""

    logger.info("backfill_started", script="files_team_id")

    stats = {
        "total_files": 0,
        "files_without_team_id": 0,
        "files_updated": 0,
        "files_failed": 0,
        "errors": []
    }

    try:
        async for db in db_manager.get_session():
            # Count total files
            result = await db.execute(select(File))
            all_files = result.scalars().all()
            stats["total_files"] = len(all_files)

            logger.info(
                "total_files_found",
                count=stats["total_files"]
            )

            # Find files without team_id
            files_without_team_id = [f for f in all_files if not hasattr(f, 'team_id') or f.team_id is None]
            stats["files_without_team_id"] = len(files_without_team_id)

            logger.info(
                "files_without_team_id",
                count=stats["files_without_team_id"]
            )

            if stats["files_without_team_id"] == 0:
                logger.info("no_files_to_backfill")
                return stats

            # Process each file
            for file_record in files_without_team_id:
                try:
                    # Look up channel to get team_id
                    channel_result = await db.execute(
                        select(Channel).where(Channel.channel_id == file_record.channel_id)
                    )
                    channel = channel_result.scalar_one_or_none()

                    if not channel:
                        logger.warning(
                            "channel_not_found_for_file",
                            file_id=file_record.file_id,
                            channel_id=file_record.channel_id
                        )
                        stats["files_failed"] += 1
                        stats["errors"].append({
                            "file_id": file_record.file_id,
                            "error": f"Channel {file_record.channel_id} not found"
                        })
                        continue

                    if not channel.team_id:
                        logger.warning(
                            "channel_missing_team_id",
                            file_id=file_record.file_id,
                            channel_id=file_record.channel_id
                        )
                        stats["files_failed"] += 1
                        stats["errors"].append({
                            "file_id": file_record.file_id,
                            "error": f"Channel {file_record.channel_id} has no team_id"
                        })
                        continue

                    # Update file with team_id
                    file_record.team_id = channel.team_id
                    stats["files_updated"] += 1

                    logger.info(
                        "file_team_id_updated",
                        file_id=file_record.file_id,
                        team_id=channel.team_id
                    )

                except Exception as e:
                    logger.error(
                        "file_backfill_error",
                        file_id=file_record.file_id,
                        error=str(e)
                    )
                    stats["files_failed"] += 1
                    stats["errors"].append({
                        "file_id": file_record.file_id,
                        "error": str(e)
                    })

            # Commit all updates
            await db.commit()

            logger.info(
                "backfill_completed",
                **stats
            )

            return stats

    except Exception as e:
        logger.error("backfill_failed", error=str(e))
        raise


async def main():
    """Main entry point"""
    print("\n" + "="*60)
    print("FILES TEAM_ID BACKFILL SCRIPT")
    print("="*60)
    print("\nThis script will backfill team_id for all file records")
    print("that are missing this critical security field.\n")

    try:
        stats = await backfill_files_team_id()

        print("\n" + "="*60)
        print("BACKFILL COMPLETED")
        print("="*60)
        print(f"\nTotal files: {stats['total_files']}")
        print(f"Files without team_id: {stats['files_without_team_id']}")
        print(f"Files updated: {stats['files_updated']}")
        print(f"Files failed: {stats['files_failed']}")

        if stats['errors']:
            print(f"\nErrors ({len(stats['errors'])}):")
            for error in stats['errors'][:10]:  # Show first 10 errors
                print(f"  - {error['file_id']}: {error['error']}")
            if len(stats['errors']) > 10:
                print(f"  ... and {len(stats['errors']) - 10} more errors")

        print("\n✓ Backfill completed successfully!\n")

    except Exception as e:
        print(f"\n✗ Backfill failed: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
