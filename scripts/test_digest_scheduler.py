#!/usr/bin/env python3
"""
Test Daily Digest Scheduler

Tests:
1. Scheduler starts correctly
2. Can trigger immediate digest
3. Scheduler status is reported correctly
4. Digest API endpoints work
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import AsyncSessionLocal
from app.services.digest_scheduler import digest_scheduler
from app.services.personalized_digest_service import PersonalizedDigestService


async def test_scheduler():
    """Test digest scheduler functionality"""
    print("=" * 80)
    print("TESTING DAILY DIGEST SCHEDULER")
    print("=" * 80)

    try:
        # Test 1: Start scheduler
        print("\n[TEST 1] Starting digest scheduler...")
        await digest_scheduler.start()
        print("✓ Scheduler started successfully")

        # Test 2: Check status
        print("\n[TEST 2] Checking scheduler status...")
        status = digest_scheduler.get_status()
        print(f"✓ Status: {status}")
        print(f"  - Running: {status['is_running']}")
        print(f"  - Next run: {status['next_run_time']}")
        print(f"  - Scheduled jobs: {status['scheduled_jobs']}")

        # Test 3: Trigger immediate digest (manual test)
        print("\n[TEST 3] Would you like to trigger an immediate digest? (y/n)")
        response = input().strip().lower()

        if response == 'y':
            print("\nEnter team_id:")
            team_id = input().strip()

            print(f"\nTriggering immediate digest for team: {team_id}...")
            sent_count = await digest_scheduler.trigger_immediate(
                team_id=team_id,
                hours_back=24
            )
            print(f"✓ Sent {sent_count} digests")

        # Test 4: Update schedule (optional)
        print("\n[TEST 4] Would you like to update the schedule? (y/n)")
        response = input().strip().lower()

        if response == 'y':
            print("\nEnter hour (0-23):")
            hour = int(input().strip())
            print("Enter minute (0-59):")
            minute = int(input().strip())

            await digest_scheduler.update_schedule(hour=hour, minute=minute)
            new_status = digest_scheduler.get_status()
            print(f"✓ Schedule updated. Next run: {new_status['next_run_time']}")

        # Test 5: Test digest generation directly
        print("\n[TEST 5] Testing digest generation directly...")
        async with AsyncSessionLocal() as db:
            digest_service = PersonalizedDigestService(db)

            print("Enter user_id:")
            user_id = input().strip()
            print("Enter team_id:")
            team_id = input().strip()

            digest = await digest_service.generate_personalized_digest(
                user_id=user_id,
                team_id=team_id,
                hours_back=24
            )

            if digest:
                print("✓ Digest generated successfully")
                print(f"\nDigest summary:")
                print(f"  - Sections: {len(digest.get('sections', []))}")
                print(f"  - Slack blocks: {len(digest.get('slack_blocks', []))}")
                print(f"  - Priority score: {digest.get('priority_score', 0):.2f}")

                # Show sections
                for section in digest.get('sections', []):
                    print(f"\n  Section: {section['title']}")
                    print(f"    Items: {len(section['items'])}")
            else:
                print("✗ No digest generated (user may have muted or no content)")

        print("\n" + "=" * 80)
        print("ALL TESTS COMPLETED")
        print("=" * 80)

        # Stop scheduler
        print("\nStopping scheduler...")
        await digest_scheduler.stop()
        print("✓ Scheduler stopped")

    except Exception as e:
        print(f"\n✗ Error during testing: {e}")
        import traceback
        traceback.print_exc()

        # Cleanup
        try:
            await digest_scheduler.stop()
        except:
            pass


async def test_api_endpoints():
    """Test digest API endpoints"""
    print("\n" + "=" * 80)
    print("TESTING DIGEST API ENDPOINTS")
    print("=" * 80)

    try:
        import httpx

        base_url = "http://localhost:8000"

        print("\nNOTE: Make sure the server is running on localhost:8000")
        print("Press Enter to continue...")
        input()

        async with httpx.AsyncClient() as client:
            # Test 1: Get preferences
            print("\n[TEST 1] GET /api/digest/preferences/{user_id}/{team_id}")
            user_id = input("Enter user_id: ").strip()
            team_id = input("Enter team_id: ").strip()

            response = await client.get(
                f"{base_url}/api/digest/preferences/{user_id}/{team_id}"
            )
            print(f"Status: {response.status_code}")
            print(f"Response: {response.json()}")

            # Test 2: Mute digest
            print("\n[TEST 2] POST /api/digest/mute/{user_id}/{team_id}")
            response = await client.post(
                f"{base_url}/api/digest/mute/{user_id}/{team_id}"
            )
            print(f"Status: {response.status_code}")
            print(f"Response: {response.json()}")

            # Test 3: Get preferences again (should show muted)
            print("\n[TEST 3] GET /api/digest/preferences/{user_id}/{team_id} (after mute)")
            response = await client.get(
                f"{base_url}/api/digest/preferences/{user_id}/{team_id}"
            )
            print(f"Status: {response.status_code}")
            prefs = response.json()
            print(f"daily_digest_enabled: {prefs['daily_digest_enabled']}")

            # Test 4: Unmute
            print("\n[TEST 4] POST /api/digest/unmute/{user_id}/{team_id}")
            response = await client.post(
                f"{base_url}/api/digest/unmute/{user_id}/{team_id}"
            )
            print(f"Status: {response.status_code}")
            print(f"Response: {response.json()}")

            # Test 5: Scheduler status
            print("\n[TEST 5] GET /api/digest/status")
            response = await client.get(f"{base_url}/api/digest/status")
            print(f"Status: {response.status_code}")
            print(f"Response: {response.json()}")

            # Test 6: Preview digest
            print("\n[TEST 6] POST /api/digest/preview/{user_id}/{team_id}")
            response = await client.post(
                f"{base_url}/api/digest/preview/{user_id}/{team_id}?hours_back=24"
            )
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'success':
                    print(f"Digest sections: {len(data['digest'].get('sections', []))}")
                else:
                    print(f"Message: {data['message']}")

        print("\n" + "=" * 80)
        print("API TESTS COMPLETED")
        print("=" * 80)

    except Exception as e:
        print(f"\n✗ Error during API testing: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("\nWhat would you like to test?")
    print("1. Scheduler functionality")
    print("2. API endpoints")
    print("3. Both")
    choice = input("\nChoice (1/2/3): ").strip()

    if choice in ["1", "3"]:
        asyncio.run(test_scheduler())

    if choice in ["2", "3"]:
        asyncio.run(test_api_endpoints())
