#!/usr/bin/env python3
"""
Test fetching real user names for DMs
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
    """Test user name fetching"""

    team_id = "T0420EE1VQ8"

    print(f"\n{'='*80}")
    print(f"TESTING USER NAME FETCHING FOR DMs")
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

            # Sample user IDs from the DMs
            sample_user_ids = [
                "U09LYALUYF2",
                "U09CFHRNFL3",
                "USLACKBOT"
            ]

            print("Fetching user names...\n")

            async with httpx.AsyncClient() as client:
                for user_id in sample_user_ids:
                    response = await client.post(
                        "https://slack.com/api/users.info",
                        headers={"Authorization": f"Bearer {user_token}"},
                        data={"user": user_id}
                    )
                    data = response.json()

                    if data.get("ok"):
                        user = data.get("user", {})
                        real_name = user.get("real_name") or user.get("profile", {}).get("real_name")
                        display_name = user.get("profile", {}).get("display_name")
                        name = display_name or real_name or user_id
                        is_bot = user.get("is_bot", False)
                        is_app_user = user.get("is_app_user", False)

                        print(f"User ID: {user_id}")
                        print(f"  Real Name: {real_name}")
                        print(f"  Display Name: {display_name}")
                        print(f"  Final Name: {name}")
                        print(f"  Is Bot: {is_bot}")
                        print(f"  Is App User: {is_app_user}")
                        print(f"  Should Filter: {'YES ❌' if is_bot or is_app_user else 'NO ✅'}")
                        print()
                    else:
                        print(f"❌ Failed to fetch {user_id}: {data.get('error')}\n")

            break

    except Exception as e:
        logger.error("test_user_names_error", error=str(e))
        print(f"\n❌ Error: {str(e)}")
        return 1
    finally:
        await db_manager.close()

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
