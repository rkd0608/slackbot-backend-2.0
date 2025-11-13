#!/usr/bin/env python3
"""
Test script to manually trigger file download and see detailed logs
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
from app.services.file_processor import file_processor
from app.services.slack_client import slack_client_manager

# Setup logging
setup_logging()
logger = get_logger(__name__)


async def test_download():
    """Test file download for a specific file"""

    file_id = "F09LC9UQZM2"

    print(f"\n{'='*60}")
    print(f"Testing file download for: {file_id}")
    print(f"{'='*60}\n")

    # Initialize database and Slack client
    db_manager.initialize()
    slack_client_manager.initialize()

    async for db in db_manager.get_session():
        # Get the file
        result = await db.execute(
            select(File).where(File.file_id == file_id)
        )
        file_record = result.scalar_one_or_none()

        if not file_record:
            print(f"❌ File {file_id} not found in database")
            return

        print(f"File info:")
        print(f"  ID: {file_record.file_id}")
        print(f"  Filename: {file_record.filename}")
        print(f"  URL: {file_record.slack_url}")
        print(f"  Team ID: {file_record.team_id}")
        print(f"  Channel ID: {file_record.channel_id}")
        print(f"  Is Processed: {file_record.is_processed}")
        print(f"  Is Downloaded: {file_record.is_downloaded}")
        print(f"  Processing Error: {file_record.processing_error}")
        print()

        # Fetch fresh file info from Slack to get public URL
        print("Fetching fresh file info from Slack...")
        file_info = await slack_client_manager.get_file_info(file_record.file_id)

        if not file_info:
            print("❌ Failed to fetch file info from Slack")
            return None

        print(f"File URLs available:")
        print(f"  url_private: {file_info.get('url_private', 'N/A')[:80]}")
        print(f"  url_private_download: {file_info.get('url_private_download', 'N/A')[:80]}")
        print(f"  permalink_public: {file_info.get('permalink_public', 'N/A')[:80]}")
        print()

        print(f"Processing file...")
        result_file = await file_processor.process_file(
            file_info=file_info,
            channel_id=file_record.channel_id,
            user_id=file_record.user_id,
            team_id=file_record.team_id,
            db=db,
            message_id=file_record.message_id
        )

        if result_file:
            print(f"\n✅ File processing completed")
            print(f"  Is Downloaded: {result_file.is_downloaded}")
            print(f"  Is Processed: {result_file.is_processed}")
            print(f"  Has Text: {bool(result_file.extracted_text)}")
            print(f"  Has Vector: {bool(result_file.vector_id)}")
            print(f"  Processing Error: {result_file.processing_error}")
        else:
            print(f"\n❌ File processing failed")

        print(f"\n{'='*60}\n")

        return result_file


if __name__ == "__main__":
    asyncio.run(test_download())