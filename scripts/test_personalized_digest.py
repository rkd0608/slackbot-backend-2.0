"""
Test Personalized Digest Service

Demonstrates personalized digest generation for individual users
"""
import asyncio
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.services.personalized_digest_service import get_personalized_digest_service
from app.core.logging import get_logger

logger = get_logger(__name__)


async def test_personalized_digest():
    """Test personalized digest generation"""
    print("\n" + "="*60)
    print("TEST: Generate Personalized Digest")
    print("="*60)

    team_id = "T0420EE1VQ8"
    # Get a real user ID from messages

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            # Get a sample user ID
            from app.models.message import Message
            from sqlalchemy import select

            result = await db.execute(
                select(Message.user_id).where(
                    Message.team_id == team_id,
                    Message.user_id.isnot(None)
                ).limit(1)
            )
            user_id = result.scalar_one_or_none()

            if not user_id:
                print("❌ No users found in database")
                break

            print(f"\nTesting with user: {user_id}")

            service = get_personalized_digest_service(db)

            # Generate personalized digest
            digest = await service.generate_personalized_digest(
                user_id=user_id,
                team_id=team_id,
                hours_back=24
            )

            if not digest:
                print("❌ Digest was not generated (might be disabled)")
                break

            print(f"\n✓ Personalized digest generated successfully")
            print(f"\nSummary: {digest['summary']}")

            print(f"\n💬 Your Mentions: {len(digest.get('mentions', []))}")
            for mention in digest.get('mentions', [])[:3]:
                print(f"  - {mention['text'][:80]}...")

            print(f"\n🧵 Your Active Threads: {len(digest.get('my_threads', []))}")
            for thread in digest.get('my_threads', [])[:3]:
                print(f"  - {thread['text'][:80]}...")
                print(f"    You replied {thread['user_replies']} times")

            print(f"\n🔥 Hot Topics: {len(digest.get('hot_topics', []))}")
            for topic in digest.get('hot_topics', [])[:3]:
                print(f"  - {topic['entity']}: {topic['mention_count']} mentions")

            print(f"\n❓ Unresolved Questions: {len(digest.get('unresolved_questions', []))}")
            for q in digest.get('unresolved_questions', [])[:2]:
                print(f"  - {q['text'][:80]}...")

            # Test Slack formatting
            print(f"\n\n" + "="*60)
            print("Slack Blocks Preview:")
            print("="*60)

            blocks = await service.format_for_slack(digest)
            print(f"\n✓ Generated {len(blocks)} Slack blocks")
            print("\nBlock sections:")
            for i, block in enumerate(blocks, 1):
                block_type = block.get('type')
                if block_type == 'section' and 'text' in block:
                    text = block['text'].get('text', '')[:60]
                    print(f"  {i}. {text}...")
                elif block_type == 'header':
                    print(f"  {i}. [HEADER] {block['text'].get('text')}")
                elif block_type == 'divider':
                    print(f"  {i}. [DIVIDER]")

            # Save output
            output_file = Path(__file__).parent / "personalized_digest_output.json"
            with open(output_file, 'w') as f:
                json.dump({
                    'digest': digest,
                    'slack_blocks': blocks
                }, f, indent=2, default=str)

            print(f"\n💾 Full output saved to: {output_file}")

        except Exception as e:
            logger.error("test_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def test_user_preferences():
    """Test user preferences creation and retrieval"""
    print("\n" + "="*60)
    print("TEST: User Preferences")
    print("="*60)

    team_id = "T0420EE1VQ8"
    test_user_id = "U_TEST_123"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            service = get_personalized_digest_service(db)

            # Create/get preferences
            prefs = await service._get_or_create_preferences(test_user_id, team_id)

            print(f"\n✓ Preferences loaded")
            print(f"  Daily digest enabled: {prefs.daily_digest_enabled}")
            print(f"  Frequency: {prefs.digest_frequency}")
            print(f"  Include hot topics: {prefs.include_hot_topics}")
            print(f"  Include mentions: {prefs.include_mentions}")
            print(f"  Last sent: {prefs.last_digest_sent}")

            # Test muting
            print(f"\n🔕 Testing mute...")
            prefs.daily_digest_enabled = False
            await db.commit()

            # Try to generate (should return None)
            digest = await service.generate_personalized_digest(
                user_id=test_user_id,
                team_id=team_id
            )

            if digest is None:
                print(f"✓ Digest correctly skipped for muted user")
            else:
                print(f"❌ Digest was generated despite being muted")

            # Re-enable
            prefs.daily_digest_enabled = True
            await db.commit()

        except Exception as e:
            logger.error("test_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("PERSONALIZED DIGEST TEST SUITE")
    print("="*60)

    await test_user_preferences()
    await test_personalized_digest()

    print("\n" + "="*60)
    print("✅ All tests completed")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
