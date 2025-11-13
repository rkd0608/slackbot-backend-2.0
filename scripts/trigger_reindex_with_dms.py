#!/usr/bin/env python3
"""
Trigger re-indexing to capture DMs and group messages

This script resets the indexing status and triggers a new indexing job
to capture all workspace data including DMs and group messages.
"""
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from sqlalchemy import select
from app.core.database import db_manager
from app.models.workspace import Workspace
from app.models.integration_status import IntegrationStatus
from app.core.queue import queue_manager
from app.core.logging import get_logger

logger = get_logger(__name__)


async def main():
    """Trigger re-indexing with DM and group message support"""

    team_id = "T0420EE1VQ8"

    print(f"\n{'='*80}")
    print(f"RE-INDEXING WITH DM & GROUP MESSAGE SUPPORT")
    print(f"{'='*80}\n")

    # Initialize database and queue
    db_manager.initialize()
    await queue_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                print(f"❌ Workspace not found: {team_id}")
                return 1

            print(f"Workspace: {workspace.team_name} ({team_id})")
            print(f"Current indexing status: {workspace.indexing_status}\n")

            # Get integration status
            stmt = select(IntegrationStatus).where(
                IntegrationStatus.team_id == team_id
            )
            result = await db.execute(stmt)
            integration_status = result.scalar_one_or_none()

            if not integration_status:
                print(f"❌ Integration status not found")
                return 1

            # Reset indexing status
            print("Resetting indexing status...")
            integration_status.indexing_status = "pending"
            integration_status.indexing_started_at = None
            integration_status.indexing_completed_at = None
            integration_status.last_checkpoint = None
            integration_status.messages_indexed = 0
            integration_status.items_indexed = 0

            workspace.indexing_status = "pending"
            workspace.indexing_started_at = None
            workspace.indexing_completed_at = None
            workspace.total_messages_indexed = 0
            workspace.total_channels_indexed = 0

            await db.commit()

            print("✅ Indexing status reset\n")

            # Get selected channels from config (keep existing selection)
            selected_channels = integration_status.config.get("selected_channels", []) if integration_status.config else []

            print(f"Selected channels: {len(selected_channels)}")
            print(f"Note: Now will ALSO index DMs and group messages!\n")

            # Trigger indexing job
            print("Publishing indexing job to queue...")

            success = await queue_manager.publish(
                queue=queue_manager.PROCESSING_QUEUE,
                message={
                    "type": "initial_workspace_indexing",
                    "team_id": team_id,
                    "workspace_id": workspace.id,
                    "bot_token": workspace.bot_access_token,
                    "channel_ids": selected_channels if selected_channels else None
                },
                routing_key="workspace.indexing.initial"
            )

            if success:
                print(f"✅ Indexing job published successfully!")
                print(f"\nWhat's happening:")
                print(f"  1. Fetching ALL conversation types (channels, DMs, groups)")
                print(f"  2. Indexing messages from all accessible conversations")
                print(f"  3. Building complete workspace knowledge graph")
                print(f"\nMonitor progress:")
                print(f"  docker logs slackbot-backend-v20-workers-1 -f")
            else:
                print(f"❌ Failed to publish indexing job")
                return 1

            break

    except Exception as e:
        logger.error("reindex_script_error", error=str(e))
        print(f"\n❌ Error: {str(e)}")
        return 1
    finally:
        await queue_manager.close()
        await db_manager.close()

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
