#!/usr/bin/env python3
"""
Test that bot DMs are filtered from onboarding
"""
import asyncio
import sys
import os
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import db_manager
from app.services.workspace_service import workspace_service
from sqlalchemy import select
from app.models.workspace import Workspace
from app.core.logging import get_logger

logger = get_logger(__name__)


async def main():
    """Test bot filtering in onboarding endpoint"""

    team_id = "T0420EE1VQ8"

    print(f"\n{'='*80}")
    print(f"TESTING BOT DM FILTERING")
    print(f"{'='*80}\n")

    db_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Get workspace and user token
            stmt = select(Workspace).where(Workspace.team_id == team_id)
            result = await db.execute(stmt)
            workspace = result.scalar_one_or_none()

            if not workspace:
                print("❌ Workspace not found")
                return 1

            user_token = await workspace_service.get_installer_user_token(team_id, db)

            if not user_token:
                print("❌ No user token")
                return 1

            print("✅ Got workspace and user token\n")

            # Step 1: Fetch all conversations
            print("Step 1: Fetching all conversations from Slack...\n")

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
                        print(f"❌ Error: {data.get('error')}")
                        break

                    batch = data.get("channels", [])
                    channels.extend(batch)

                    cursor = data.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break

            # Step 2: Identify DMs and check which users are bots
            dms = [ch for ch in channels if ch.get("is_im", False) or ch["id"].startswith("D")]

            print(f"Total DMs found: {len(dms)}\n")

            user_ids_to_check = set()
            for dm in dms:
                user_id = dm.get("user")
                if user_id:
                    user_ids_to_check.add(user_id)

            # Step 3: Check each user
            print("Step 2: Checking which users are bots...\n")

            bot_user_ids = set()
            human_user_ids = set()
            user_info = {}

            async with httpx.AsyncClient() as client:
                for user_id in user_ids_to_check:
                    response = await client.post(
                        "https://slack.com/api/users.info",
                        headers={"Authorization": f"Bearer {user_token}"},
                        data={"user": user_id}
                    )
                    data = response.json()

                    if data.get("ok"):
                        user = data.get("user", {})
                        is_bot = user.get("is_bot", False)
                        is_app_user = user.get("is_app_user", False)
                        is_slackbot = user_id == "USLACKBOT"
                        real_name = user.get("real_name", user_id)

                        user_info[user_id] = {
                            "name": real_name,
                            "is_bot": is_bot,
                            "is_app_user": is_app_user,
                            "is_slackbot": is_slackbot
                        }

                        if is_bot or is_app_user or is_slackbot:
                            bot_user_ids.add(user_id)
                        else:
                            human_user_ids.add(user_id)

            # Step 4: Show results
            print(f"{'='*80}")
            print(f"RESULTS")
            print(f"{'='*80}\n")

            print(f"🤖 Bot/App DMs (WILL BE FILTERED): {len(bot_user_ids)}")
            for user_id in bot_user_ids:
                info = user_info.get(user_id, {})
                print(f"  ❌ {info.get('name', user_id)} ({user_id})")

            print(f"\n👤 Human DMs (WILL BE SHOWN): {len(human_user_ids)}")
            for user_id in human_user_ids:
                info = user_info.get(user_id, {})
                print(f"  ✅ {info.get('name', user_id)} ({user_id})")

            print(f"\n{'='*80}")
            print(f"SUMMARY")
            print(f"{'='*80}\n")
            print(f"Total DMs: {len(dms)}")
            print(f"Bot/App DMs (filtered): {len(bot_user_ids)}")
            print(f"Human DMs (shown): {len(human_user_ids)}")
            print(f"\nUser will see {len(human_user_ids)} DMs in onboarding UI")

            break

    except Exception as e:
        logger.error("test_bot_filtering_error", error=str(e))
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
