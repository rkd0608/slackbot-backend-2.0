#!/usr/bin/env python3
"""
List ALL conversations in the workspace to see what's available
"""
import asyncio
import sys
import os
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import db_manager
from app.services.workspace_service import workspace_service
from app.core.logging import get_logger

logger = get_logger(__name__)


async def main():
    """List all conversations from Slack API"""

    team_id = "T0420EE1VQ8"

    print(f"\n{'='*80}")
    print(f"LISTING ALL CONVERSATIONS IN WORKSPACE")
    print(f"{'='*80}\n")

    db_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Get user token
            user_token = await workspace_service.get_installer_user_token(team_id, db)

            if not user_token:
                print("❌ No user token found")
                return 1

            print(f"✅ Got user token\n")

            async with httpx.AsyncClient() as client:
                cursor = None
                all_conversations = []

                print("Fetching all conversations...\n")

                while True:
                    params = {
                        "types": "public_channel,private_channel,im,mpim",
                        "exclude_archived": False,
                        "limit": 200
                    }

                    if cursor:
                        params["cursor"] = cursor

                    response = await client.post(
                        "https://slack.com/api/conversations.list",
                        headers={"Authorization": f"Bearer {user_token}"},
                        data=params
                    )

                    data = response.json()

                    if not data.get("ok"):
                        print(f"❌ Error: {data.get('error')}")
                        break

                    batch = data.get("channels", [])
                    all_conversations.extend(batch)

                    cursor = data.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break

                # Categorize conversations
                channels = []
                private_channels = []
                dms = []
                groups = []

                for conv in all_conversations:
                    conv_id = conv["id"]
                    is_dm = conv.get("is_im", False) or conv_id.startswith("D")
                    is_mpim = conv.get("is_mpim", False)
                    is_private = conv.get("is_private", False)

                    if is_dm:
                        dms.append(conv)
                    elif is_mpim:
                        groups.append(conv)
                    elif is_private:
                        private_channels.append(conv)
                    else:
                        channels.append(conv)

                print(f"{'='*80}")
                print(f"SUMMARY")
                print(f"{'='*80}\n")
                print(f"Public Channels: {len(channels)}")
                print(f"Private Channels: {len(private_channels)}")
                print(f"Direct Messages: {len(dms)}")
                print(f"Group Messages: {len(groups)}")
                print(f"\nTotal: {len(all_conversations)}")

                print(f"\n{'='*80}")
                print(f"PUBLIC CHANNELS")
                print(f"{'='*80}\n")
                for ch in channels:
                    print(f"  {ch.get('name', 'unnamed')} ({ch['id']})")

                if private_channels:
                    print(f"\n{'='*80}")
                    print(f"PRIVATE CHANNELS")
                    print(f"{'='*80}\n")
                    for ch in private_channels:
                        print(f"  {ch.get('name', 'unnamed')} ({ch['id']})")

                print(f"\n{'='*80}")
                print(f"DIRECT MESSAGES")
                print(f"{'='*80}\n")
                for dm in dms:
                    user_id = dm.get("user", "unknown")
                    print(f"  DM with {user_id} ({dm['id']})")

                if groups:
                    print(f"\n{'='*80}")
                    print(f"GROUP MESSAGES (MPIM)")
                    print(f"{'='*80}\n")
                    for grp in groups:
                        print(f"  {grp.get('name', 'unnamed')} ({grp['id']})")

            break

    except Exception as e:
        logger.error("list_conversations_error", error=str(e))
        print(f"\n❌ Error: {str(e)}")
        return 1
    finally:
        await db_manager.close()

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
