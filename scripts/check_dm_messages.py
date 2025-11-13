#!/usr/bin/env python3
"""
Check if selected DMs actually have messages in Slack
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
    """Check DM message counts from Slack API"""

    team_id = "T0420EE1VQ8"

    # Selected DMs from config
    selected_dms = [
        "D09H7A3HKEJ",
        "D09H79ST7F0",
        "D09H4M1CV29",
        "D09CFHRQX7D",
        "D09CFHRP9AP"
    ]

    print(f"\n{'='*80}")
    print(f"CHECKING DM MESSAGE COUNTS FROM SLACK API")
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
                for dm_id in selected_dms:
                    print(f"Checking {dm_id}...")

                    # Fetch conversation history
                    response = await client.post(
                        "https://slack.com/api/conversations.history",
                        headers={"Authorization": f"Bearer {user_token}"},
                        data={
                            "channel": dm_id,
                            "limit": 1000
                        }
                    )

                    data = response.json()

                    if data.get("ok"):
                        messages = data.get("messages", [])
                        print(f"  ✅ {len(messages)} messages found")
                    else:
                        error = data.get("error")
                        print(f"  ❌ Error: {error}")

                    await asyncio.sleep(0.3)  # Rate limiting

            break

    except Exception as e:
        logger.error("check_dms_error", error=str(e))
        print(f"\n❌ Error: {str(e)}")
        return 1
    finally:
        await db_manager.close()

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
