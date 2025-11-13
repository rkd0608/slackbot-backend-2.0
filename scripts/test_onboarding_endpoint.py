#!/usr/bin/env python3
"""
Test the onboarding endpoint directly to see what's being filtered
"""
import asyncio
import sys
import os
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import db_manager
from sqlalchemy import select
from app.models.workspace import Workspace
from app.core.logging import get_logger

logger = get_logger(__name__)


async def main():
    """Test onboarding endpoint logic"""

    team_id = "T0420EE1VQ8"

    print(f"\n{'='*80}")
    print(f"TESTING ONBOARDING CHANNEL FETCH")
    print(f"{'='*80}\n")

    db_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Get workspace
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                print("❌ Workspace not found")
                return 1

            print(f"✅ Got workspace: {workspace.team_name}\n")

            # Get user token (same as updated onboarding endpoint)
            from app.services.workspace_service import workspace_service
            user_token = await workspace_service.get_installer_user_token(team_id, db)

            if not user_token:
                print("⚠️  No user token, using bot token (limited visibility)")
                user_token = workspace.bot_access_token
            else:
                print("✅ Using user token (full visibility)\n")

            # Fetch channels via Slack API (same as onboarding endpoint)
            channels = []
            cursor = None

            async with httpx.AsyncClient() as client:
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
                        print(f"❌ Slack API Error: {data.get('error')}")
                        break

                    batch = data.get("channels", [])
                    print(f"Fetched batch: {len(batch)} conversations")
                    channels.extend(batch)

                    cursor = data.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break

            print(f"\nTotal fetched from Slack: {len(channels)}\n")

            # Format response (same as onboarding endpoint)
            formatted_channels = []
            for ch in channels:
                channel_id = ch["id"]
                is_dm = ch.get("is_im", False) or channel_id.startswith("D")
                is_mpim = ch.get("is_mpim", False)

                # Handle naming for different conversation types
                if is_dm:
                    user_id = ch.get("user", "unknown")
                    name = f"DM with {user_id}"
                else:
                    name = ch.get("name", f"Unnamed-{channel_id}")

                formatted_channels.append({
                    "channel_id": channel_id,
                    "name": name,
                    "is_private": ch.get("is_private", False) or is_dm,
                    "is_dm": is_dm,
                    "is_group": is_mpim,
                    "is_archived": ch.get("is_archived", False),
                    "member_count": ch.get("num_members", 0)
                })

            print(f"After formatting: {len(formatted_channels)} conversations\n")

            # Show breakdown
            dms = [c for c in formatted_channels if c["is_dm"]]
            groups = [c for c in formatted_channels if c["is_group"]]
            regular = [c for c in formatted_channels if not c["is_dm"] and not c["is_group"]]

            print(f"{'='*80}")
            print(f"BREAKDOWN")
            print(f"{'='*80}\n")
            print(f"Channels: {len(regular)}")
            print(f"DMs: {len(dms)}")
            print(f"Groups: {len(groups)}")
            print(f"\nTotal: {len(formatted_channels)}")

            # Show all conversations
            print(f"\n{'='*80}")
            print(f"ALL CONVERSATIONS")
            print(f"{'='*80}\n")

            for conv in formatted_channels:
                conv_type = "DM" if conv["is_dm"] else ("Group" if conv["is_group"] else "Channel")
                archived = " [ARCHIVED]" if conv["is_archived"] else ""
                print(f"  [{conv_type}] {conv['name']} ({conv['channel_id']}){archived}")

            break

    except Exception as e:
        logger.error("test_onboarding_error", error=str(e))
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await db_manager.close()

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
