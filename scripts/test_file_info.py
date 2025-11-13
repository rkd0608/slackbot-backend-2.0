#!/usr/bin/env python3
"""
Test script to fetch complete file information from Slack API
"""
import asyncio
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.slack_client import slack_client_manager
from app.core.config import settings

async def test_file_info():
    """Fetch and display complete file info from Slack"""

    # Initialize Slack client
    slack_client_manager.initialize()

    # Test file ID from database
    file_id = "F09LC9UQZM2"

    print(f"\n{'='*60}")
    print(f"Fetching file info for: {file_id}")
    print(f"{'='*60}\n")

    # Get file info
    file_info = await slack_client_manager.get_file_info(file_id)

    if not file_info:
        print("❌ Failed to fetch file info")
        return

    print("✅ File info fetched successfully\n")

    # Display all URL-related fields
    print("URL Fields:")
    print("-" * 60)
    url_fields = [
        'url_private',
        'url_private_download',
        'permalink',
        'permalink_public',
        'url_download'
    ]

    for field in url_fields:
        value = file_info.get(field)
        if value:
            print(f"{field:25s}: {value}")

    # Display file metadata
    print(f"\n{'='*60}")
    print("File Metadata:")
    print("-" * 60)
    metadata_fields = ['id', 'name', 'title', 'mimetype', 'filetype', 'size', 'mode', 'is_external', 'external_type']
    for field in metadata_fields:
        value = file_info.get(field)
        if value is not None:
            print(f"{field:25s}: {value}")

    # Display complete JSON (pretty-printed, sensitive data masked)
    print(f"\n{'='*60}")
    print("Complete File Info (JSON):")
    print("-" * 60)

    # Mask sensitive data
    safe_info = file_info.copy()
    if 'url_private' in safe_info:
        safe_info['url_private'] = safe_info['url_private'][:100] + '...'
    if 'url_private_download' in safe_info:
        safe_info['url_private_download'] = safe_info['url_private_download'][:100] + '...'

    print(json.dumps(safe_info, indent=2))

    # Test download attempt
    print(f"\n{'='*60}")
    print("Testing File Download:")
    print("-" * 60)

    download_url = file_info.get('url_private_download') or file_info.get('url_private')
    if download_url:
        print(f"Download URL: {download_url[:100]}...")

        file_data = await slack_client_manager.download_file(download_url)

        if file_data:
            print(f"✅ Download successful!")
            print(f"   Size: {len(file_data)} bytes")
            print(f"   First 100 bytes: {file_data[:100]}")
        else:
            print(f"❌ Download failed")
    else:
        print("❌ No download URL available")

    print(f"\n{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(test_file_info())
