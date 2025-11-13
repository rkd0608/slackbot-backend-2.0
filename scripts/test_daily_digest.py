"""
Test Daily Digest Service

Demonstrates the daily digest generation and formatting
"""
import asyncio
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.services.daily_digest_service import get_daily_digest_service
from app.core.logging import get_logger

logger = get_logger(__name__)


async def test_generate_digest():
    """Test digest generation"""
    print("\n" + "="*60)
    print("TEST: Generate Daily Digest")
    print("="*60)

    team_id = "T0420EE1VQ8"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            service = get_daily_digest_service(db)

            # Generate digest for last 24 hours
            digest = await service.generate_digest(
                team_id=team_id,
                hours_back=24,
                min_mentions=1  # Lower threshold for testing
            )

            print(f"\n✓ Digest generated successfully")
            print(f"\nSummary: {digest['summary']}")

            print(f"\n📊 Hot Topics: {len(digest['hot_topics'])}")
            for topic in digest['hot_topics'][:5]:
                print(f"  - {topic['entity']} ({topic['entity_type']}): "
                      f"{topic['mention_count']} mentions, "
                      f"{topic['github_connections']} GitHub links")

            print(f"\n💬 Active Discussions: {len(digest['active_discussions'])}")
            for disc in digest['active_discussions'][:3]:
                print(f"  - {disc['text'][:80]}...")
                print(f"    {disc['reply_count']} replies, {disc['reaction_count']} reactions")

            print(f"\n🔗 Cross-Source Links: {len(digest['cross_source_links'])}")
            for link in digest['cross_source_links'][:3]:
                print(f"  - {link['source']['title']} ({link['source']['source']}) "
                      f"→ {link['target']['title']} ({link['target']['source']})")

            print(f"\n⭐ Expert Activity: {len(digest['expert_activity'])}")
            for expert in digest['expert_activity'][:3]:
                print(f"  - User {expert['user_id']}: {expert['message_count']} messages")

            print(f"\n❓ Unresolved Questions: {len(digest['unresolved_questions'])}")
            for q in digest['unresolved_questions'][:3]:
                print(f"  - {q['text'][:80]}...")

            # Test Slack formatting
            print(f"\n\n" + "="*60)
            print("Slack Blocks Preview:")
            print("="*60)

            blocks = await service.format_for_slack(digest)
            print(f"\n✓ Generated {len(blocks)} Slack blocks")
            print("\nBlock types:")
            for i, block in enumerate(blocks, 1):
                block_type = block.get('type')
                text_preview = ""
                if block_type == 'section' and 'text' in block:
                    text_preview = block['text'].get('text', '')[:60]
                elif block_type == 'header' and 'text' in block:
                    text_preview = block['text'].get('text', '')

                print(f"  {i}. {block_type}: {text_preview}...")

            # Save to file for inspection
            output_file = Path(__file__).parent / "digest_output.json"
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


async def main():
    """Run tests"""
    print("\n" + "="*60)
    print("DAILY DIGEST TEST SUITE")
    print("="*60)

    await test_generate_digest()

    print("\n" + "="*60)
    print("✅ Test completed")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
