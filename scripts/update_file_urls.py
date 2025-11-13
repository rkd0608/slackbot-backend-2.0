#!/usr/bin/env python3
"""
Script to update file URLs to use url_private_download
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.core.database import db_manager
from app.core.logging import get_logger, setup_logging
from app.models.file import File
from app.services.slack_client import slack_client_manager

# Setup logging
setup_logging()
logger = get_logger(__name__)


async def update_file_urls():
    """Update all file URLs to use url_private_download from Slack API"""

    print(f"\n{'='*60}")
    print("FILE URL UPDATE SCRIPT")
    print(f"{'='*60}\n")

    # Initialize Slack client
    slack_client_manager.initialize()

    stats = {
        "total_files": 0,
        "files_updated": 0,
        "files_failed": 0,
        "errors": []
    }

    async for db in db_manager.get_session():
        # Get all files
        result = await db.execute(select(File))
        files = result.scalars().all()
        stats["total_files"] = len(files)

        print(f"Found {len(files)} files to check\n")

        for file_record in files:
            try:
                print(f"Processing file {file_record.file_id}...", end=" ")

                # Fetch fresh file info from Slack
                file_info = await slack_client_manager.get_file_info(file_record.file_id)

                if not file_info:
                    print(f"❌ Failed to fetch info from Slack")
                    stats["files_failed"] += 1
                    stats["errors"].append({
                        "file_id": file_record.file_id,
                        "error": "Failed to fetch from Slack API"
                    })
                    continue

                # Get the download URL (prefer url_private_download)
                new_url = file_info.get("url_private_download") or file_info.get("url_private")

                if not new_url:
                    print(f"❌ No download URL available")
                    stats["files_failed"] += 1
                    stats["errors"].append({
                        "file_id": file_record.file_id,
                        "error": "No download URL in Slack response"
                    })
                    continue

                # Update the file record
                old_url = file_record.slack_url
                if old_url != new_url:
                    file_record.slack_url = new_url
                    # Reset processing status to retry with new URL
                    file_record.is_processed = 0
                    file_record.is_downloaded = 0
                    file_record.processing_error = None
                    stats["files_updated"] += 1
                    print(f"✅ Updated")
                    print(f"   Old: {old_url[:80]}...")
                    print(f"   New: {new_url[:80]}...")
                else:
                    print(f"⏭️  URL unchanged")

            except Exception as e:
                print(f"❌ Error: {str(e)}")
                logger.error(
                    "file_update_error",
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

        print(f"\n{'='*60}")
        print("UPDATE COMPLETED")
        print(f"{'='*60}")
        print(f"Total files: {stats['total_files']}")
        print(f"Files updated: {stats['files_updated']}")
        print(f"Files failed: {stats['files_failed']}")

        if stats['errors']:
            print(f"\nErrors ({len(stats['errors'])}):")
            for error in stats['errors'][:10]:
                print(f"  - {error['file_id']}: {error['error']}")
            if len(stats['errors']) > 10:
                print(f"  ... and {len(stats['errors']) - 10} more errors")

        print(f"\n{'='*60}\n")

        return stats


if __name__ == "__main__":
    asyncio.run(update_file_urls())
