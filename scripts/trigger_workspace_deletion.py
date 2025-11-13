#!/usr/bin/env python3
"""
Trigger complete workspace deletion including Slack uninstallation

This script deletes all workspace data and uninstalls from Slack.
Requested by user for clean reinstallation with updated OAuth scopes.
"""
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import db_manager
from app.services.workspace_deletion_service import workspace_deletion_service
from app.core.logging import get_logger

logger = get_logger(__name__)


async def main():
    """Execute workspace deletion"""

    team_id = "T0420EE1VQ8"

    print(f"\n{'='*80}")
    print(f"WORKSPACE DELETION - Team ID: {team_id}")
    print(f"{'='*80}\n")

    print("⚠️  This will:")
    print("  1. Delete all Pinecone vectors (messages, code, files)")
    print("  2. Delete all database records")
    print("  3. Uninstall app from Slack")
    print("  4. Revoke all tokens\n")

    # Initialize database
    db_manager.initialize()

    try:
        async for db in db_manager.get_session():
            # Execute deletion
            result = await workspace_deletion_service.delete_workspace(
                team_id=team_id,
                db=db,
                delete_from_slack=True,
                admin_user_id="system_script"
            )

            # Print results
            print(f"\n{'='*80}")
            print(f"DELETION RESULTS")
            print(f"{'='*80}\n")

            print(f"Success: {result['success']}")
            print(f"Workspace: {result.get('workspace_name', 'N/A')}")

            if result.get('deleted'):
                print(f"\nDeleted Records:")
                for key, value in result['deleted'].items():
                    if isinstance(value, dict):
                        print(f"  {key}:")
                        for sub_key, sub_value in value.items():
                            print(f"    {sub_key}: {sub_value}")
                    else:
                        print(f"  {key}: {value}")

            if result.get('errors'):
                print(f"\nErrors:")
                for error in result['errors']:
                    print(f"  ❌ {error}")

            if result['success']:
                print(f"\n✅ Workspace deletion completed successfully!")
                print(f"\nNext steps:")
                print(f"  1. Frontend OAuth URL should have updated scopes")
                print(f"  2. Reinstall app via OAuth flow")
                print(f"  3. New installation will have correct user token scopes")
                print(f"  4. Indexing will work with message history access")
            else:
                print(f"\n❌ Workspace deletion failed!")

            await db.commit()
            break

    except Exception as e:
        logger.error("deletion_script_error", error=str(e))
        print(f"\n❌ Error: {str(e)}")
        return 1
    finally:
        await db_manager.close()

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
