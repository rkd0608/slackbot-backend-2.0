"""
Test Conversation Context Service

Demonstrates context-aware query rewriting
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.services.conversation_context_service import get_conversation_context_service
from app.core.logging import get_logger

logger = get_logger(__name__)


async def test_context_tracking():
    """Test conversation context tracking"""
    print("\n" + "="*60)
    print("TEST: Conversation Context Tracking")
    print("="*60)

    team_id = "T0420EE1VQ8"
    user_id = "U_TEST_CONTEXT"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            service = get_conversation_context_service(db)

            # Start conversation
            conversation = await service.get_or_create_conversation(
                user_id=user_id,
                team_id=team_id
            )

            print(f"\n✓ Conversation created: {conversation.id}")

            # Simulate conversation flow
            print("\n" + "="*60)
            print("CONVERSATION SIMULATION")
            print("="*60)

            # Query 1: User asks about authentication
            print("\n👤 User: Tell me about authentication")
            await service.add_query_to_context(
                conversation_id=conversation.id,
                query="Tell me about authentication",
                query_analysis={
                    'entities': [
                        {'text': 'authentication', 'type': 'TOPIC'}
                    ],
                    'intents': ['search'],
                    'topics': ['authentication', 'security']
                }
            )
            print("✓ Query added to context")

            # Query 2: Vague follow-up
            print("\n👤 User: What about the API?")
            rewrite_result = await service.rewrite_query_with_context(
                query="What about the API?",
                conversation_id=conversation.id
            )

            print(f"🤖 Bot understands:")
            print(f"   Original: {rewrite_result['original_query']}")
            print(f"   Rewritten: {rewrite_result['rewritten_query']}")
            print(f"   Context applied: {rewrite_result['context_applied']}")
            print(f"   Context entities: {rewrite_result['context_entities']}")

            # Add this query to context
            await service.add_query_to_context(
                conversation_id=conversation.id,
                query="What about the API?",
                query_analysis={
                    'entities': [
                        {'text': 'API', 'type': 'TECHNOLOGY'}
                    ],
                    'intents': ['search']
                }
            )

            # Query 3: Another vague follow-up
            print("\n👤 User: Any issues?")
            rewrite_result = await service.rewrite_query_with_context(
                query="Any issues?",
                conversation_id=conversation.id
            )

            print(f"🤖 Bot understands:")
            print(f"   Original: {rewrite_result['original_query']}")
            print(f"   Rewritten: {rewrite_result['rewritten_query']}")
            print(f"   Context applied: {rewrite_result['context_applied']}")
            print(f"   Context entities: {rewrite_result['context_entities']}")

            # Query 4: Specific query (shouldn't need context)
            print("\n👤 User: Show me database migration documentation")
            rewrite_result = await service.rewrite_query_with_context(
                query="Show me database migration documentation",
                conversation_id=conversation.id
            )

            print(f"🤖 Bot understands:")
            print(f"   Original: {rewrite_result['original_query']}")
            print(f"   Rewritten: {rewrite_result['rewritten_query']}")
            print(f"   Context applied: {rewrite_result['context_applied']}")

            # Get final context
            print("\n" + "="*60)
            print("FINAL CONTEXT STATE")
            print("="*60)

            context = await service.get_context_for_query(conversation.id)
            print(f"\n✓ Active entities: {context['active_entities']}")
            print(f"✓ Recent queries: {len(context['history'])}")
            for i, entry in enumerate(context['history'], 1):
                print(f"  {i}. {entry['query']}")
                print(f"     Entities: {[e['text'] for e in entry['entities']]}")

        except Exception as e:
            logger.error("test_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def test_vague_query_detection():
    """Test detection of vague queries"""
    print("\n" + "="*60)
    print("TEST: Vague Query Detection")
    print("="*60)

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            service = get_conversation_context_service(db)

            test_cases = [
                ("What about the API?", True),
                ("Show me more", True),
                ("Any issues?", True),
                ("Tell me about that", True),
                ("Show me database migration docs", False),
                ("What are the authentication endpoints?", False),
                ("How do I configure PostgreSQL?", False),
            ]

            print("\nQuery Vagueness Detection:")
            for query, expected_vague in test_cases:
                from app.services.query_service import query_service
                analysis = await query_service.analyze_query(query)
                is_vague = service._is_vague_query(query, analysis)

                status = "✓" if is_vague == expected_vague else "✗"
                vague_str = "VAGUE" if is_vague else "SPECIFIC"

                print(f"{status} '{query}'")
                print(f"   → {vague_str} (expected: {'VAGUE' if expected_vague else 'SPECIFIC'})")

        except Exception as e:
            logger.error("test_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def test_context_expiry():
    """Test conversation expiry"""
    print("\n" + "="*60)
    print("TEST: Context Expiry")
    print("="*60)

    team_id = "T0420EE1VQ8"
    user_id = "U_TEST_EXPIRY"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            service = get_conversation_context_service(db)

            # Create conversation
            conversation = await service.get_or_create_conversation(
                user_id=user_id,
                team_id=team_id
            )

            print(f"\n✓ Conversation created: {conversation.id}")

            # Manually set updated_at to old time
            from datetime import datetime, timedelta
            conversation.updated_at = datetime.utcnow() - timedelta(days=8)
            await db.commit()

            print(f"✓ Set conversation to 8 days old")

            # Clean up
            deleted = await service.cleanup_expired_conversations(
                team_id=team_id,
                days_old=7
            )

            print(f"\n✓ Cleaned up {deleted} expired conversations")

        except Exception as e:
            logger.error("test_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("CONVERSATION CONTEXT TEST SUITE")
    print("="*60)

    await test_vague_query_detection()
    await test_context_tracking()
    await test_context_expiry()

    print("\n" + "="*60)
    print("✅ All tests completed")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
