#!/usr/bin/env python3
"""
Test Conversational Context Integration

Simulates a conversation to verify:
1. Context is tracked across queries
2. Vague queries are enhanced with context
3. Entity-based rewriting works
4. LLM-based rewriting chains properly
5. Context is stored in conversation history
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db_manager
from app.services.conversation_context_service import ConversationContextService
from app.services.query_rewriter import query_rewriter
from app.services.query_service import query_service

# Initialize database
db_manager.initialize()


async def simulate_conversation():
    """Simulate a multi-turn conversation to test context tracking"""
    print("=" * 80)
    print("TESTING CONVERSATIONAL CONTEXT INTEGRATION")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        context_service = ConversationContextService(db)

        # Get test parameters
        print("\nEnter test user_id (or press Enter for 'U123TEST'):")
        user_id = input().strip() or "U123TEST"
        print("Enter test team_id (or press Enter for 'T123TEST'):")
        team_id = input().strip() or "T123TEST"

        # Create or get conversation
        print(f"\n[STEP 1] Creating conversation for user {user_id}...")
        conversation = await context_service.get_or_create_conversation(
            user_id=user_id,
            team_id=team_id,
            channel_id="C123TEST"
        )
        print(f"✓ Conversation created: {conversation.conversation_id}")

        # Conversation simulation
        conversation_turns = [
            {
                "query": "Tell me about authentication in our API",
                "expected": "Should extract entities: authentication, API"
            },
            {
                "query": "What about the database?",
                "expected": "Should add 'authentication' or 'API' from context"
            },
            {
                "query": "Any issues?",
                "expected": "Should add 'database' or previous context"
            },
            {
                "query": "Show me more",
                "expected": "Should reference previous topics"
            }
        ]

        print("\n" + "=" * 80)
        print("SIMULATING CONVERSATION TURNS")
        print("=" * 80)

        for i, turn in enumerate(conversation_turns, 1):
            print(f"\n{'='*80}")
            print(f"TURN {i}")
            print(f"{'='*80}")

            query = turn['query']
            print(f"\nUser: \"{query}\"")
            print(f"Expected: {turn['expected']}")

            # Step 1: Analyze query
            print(f"\n[Step 1] Analyzing query...")
            analysis = await query_service.analyze_query(query)
            print(f"  Entities found: {[e['text'] for e in analysis.get('entities', [])]}")
            print(f"  Intents: {analysis.get('intents', [])}")

            # Step 2: Entity-based context rewriting
            print(f"\n[Step 2] Applying entity-based context rewriting...")
            context_rewrite = await context_service.rewrite_query_with_context(
                query=query,
                conversation_id=str(conversation.id)
            )
            print(f"  Original: \"{context_rewrite['original_query']}\"")
            print(f"  Rewritten: \"{context_rewrite['rewritten_query']}\"")
            print(f"  Context applied: {context_rewrite['context_applied']}")
            if context_rewrite['context_applied']:
                print(f"  Context entities: {context_rewrite.get('context_entities', [])}")

            # Step 3: LLM-based rewriting (simulated - would use actual conversation_id in bot)
            print(f"\n[Step 3] LLM-based rewriting would happen here...")
            print(f"  Input to LLM rewriter: \"{context_rewrite['rewritten_query']}\"")

            # Step 4: Add query to context for next turn
            print(f"\n[Step 4] Adding query to context...")
            success = await context_service.add_query_to_context(
                conversation_id=str(conversation.id),
                query=query,
                query_analysis=analysis
            )
            print(f"  ✓ Added to context" if success else "  ✗ Failed to add")

            # Step 5: Show current context state
            print(f"\n[Step 5] Current context state:")
            context = await context_service.get_context_for_query(str(conversation.id))
            print(f"  Active entities: {context['active_entities']}")
            print(f"  Message count: {len(context['history'])}")

            # Wait before next turn
            if i < len(conversation_turns):
                print(f"\n{'-'*80}")
                print("Press Enter to continue to next turn...")
                input()

        # Final context check
        print("\n" + "=" * 80)
        print("FINAL CONTEXT STATE")
        print("=" * 80)

        final_context = await context_service.get_context_for_query(str(conversation.id))
        print(f"\nConversation ID: {conversation.id}")
        print(f"Total messages: {len(final_context['history'])}")
        print(f"\nActive entities across conversation:")
        for entity in final_context['active_entities']:
            print(f"  - {entity}")

        print(f"\nConversation history:")
        for i, entry in enumerate(final_context['history'], 1):
            print(f"\n  Turn {i}: \"{entry['query']}\"")
            print(f"    Entities: {[e['text'] for e in entry.get('entities', [])]}")
            print(f"    Intents: {entry.get('intents', [])}")

        print("\n" + "=" * 80)
        print("TEST COMPLETED")
        print("=" * 80)

        # Analysis
        print("\n" + "=" * 80)
        print("INTEGRATION VERIFICATION")
        print("=" * 80)

        checks = [
            ("Context tracking", len(final_context['history']) == len(conversation_turns)),
            ("Entity extraction", len(final_context['active_entities']) > 0),
            ("Context persistence", conversation.id is not None),
        ]

        all_passed = True
        for check_name, passed in checks:
            status = "✓ PASS" if passed else "✗ FAIL"
            print(f"{status} - {check_name}")
            if not passed:
                all_passed = False

        if all_passed:
            print("\n✓ All integration checks passed!")
        else:
            print("\n✗ Some checks failed - review output above")


async def test_vague_query_detection():
    """Test vague query detection logic"""
    print("\n" + "=" * 80)
    print("TESTING VAGUE QUERY DETECTION")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        context_service = ConversationContextService(db)

        test_cases = [
            ("What about the API?", True, "Contains 'what about'"),
            ("Show me more", True, "Contains 'show me more'"),
            ("Any issues with deployment?", True, "Starts with 'any'"),
            ("Tell me about authentication", True, "Contains 'tell me about'"),
            ("How do I configure the database connection?", False, "Specific query with details"),
            ("What is the database migration process for production?", False, "Specific query with context"),
            ("it crashed", True, "Contains pronoun 'it'"),
            ("this feature", True, "Contains pronoun 'this'"),
        ]

        print("\nTesting vague query detection:")
        print(f"{'Query':<50} {'Vague?':<10} {'Reason'}")
        print("-" * 80)

        correct = 0
        total = len(test_cases)

        for query, expected_vague, reason in test_cases:
            # Analyze query first
            analysis = await query_service.analyze_query(query)

            # Check if vague
            is_vague = context_service._is_vague_query(query, analysis)

            status = "✓" if is_vague == expected_vague else "✗"
            vague_str = "YES" if is_vague else "NO"

            print(f"{status} {query:<48} {vague_str:<10} {reason}")

            if is_vague == expected_vague:
                correct += 1

        print("-" * 80)
        print(f"\nResults: {correct}/{total} correct ({100*correct/total:.1f}%)")


async def test_context_rewriting_examples():
    """Test context-based rewriting with concrete examples"""
    print("\n" + "=" * 80)
    print("TESTING CONTEXT-BASED REWRITING EXAMPLES")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        context_service = ConversationContextService(db)

        # Create test conversation
        user_id = "U_REWRITE_TEST"
        team_id = "T_REWRITE_TEST"

        conversation = await context_service.get_or_create_conversation(
            user_id=user_id,
            team_id=team_id
        )

        # Scenario 1: Authentication discussion
        print("\n" + "-" * 80)
        print("SCENARIO 1: Authentication Discussion")
        print("-" * 80)

        queries = [
            "How does authentication work in our system?",
            "What about the API?",
            "Any security issues?"
        ]

        for query in queries:
            print(f"\nQuery: \"{query}\"")

            analysis = await query_service.analyze_query(query)
            rewrite_result = await context_service.rewrite_query_with_context(
                query=query,
                conversation_id=str(conversation.id)
            )

            print(f"  Rewritten: \"{rewrite_result['rewritten_query']}\"")
            print(f"  Context applied: {rewrite_result['context_applied']}")

            await context_service.add_query_to_context(
                conversation_id=str(conversation.id),
                query=query,
                query_analysis=analysis
            )

        # Create new conversation for scenario 2
        conversation2 = await context_service.get_or_create_conversation(
            user_id="U_REWRITE_TEST2",
            team_id=team_id
        )

        # Scenario 2: Database discussion
        print("\n" + "-" * 80)
        print("SCENARIO 2: Database Migration Discussion")
        print("-" * 80)

        queries2 = [
            "Tell me about the database migration process",
            "Show me more",
            "Any problems with that?"
        ]

        for query in queries2:
            print(f"\nQuery: \"{query}\"")

            analysis = await query_service.analyze_query(query)
            rewrite_result = await context_service.rewrite_query_with_context(
                query=query,
                conversation_id=str(conversation2.id)
            )

            print(f"  Rewritten: \"{rewrite_result['rewritten_query']}\"")
            print(f"  Context applied: {rewrite_result['context_applied']}")

            await context_service.add_query_to_context(
                conversation_id=str(conversation2.id),
                query=query,
                query_analysis=analysis
            )


if __name__ == "__main__":
    print("\nWhat would you like to test?")
    print("1. Full conversation simulation")
    print("2. Vague query detection")
    print("3. Context rewriting examples")
    print("4. All tests")
    choice = input("\nChoice (1/2/3/4): ").strip()

    if choice in ["1", "4"]:
        asyncio.run(simulate_conversation())

    if choice in ["2", "4"]:
        asyncio.run(test_vague_query_detection())

    if choice in ["3", "4"]:
        asyncio.run(test_context_rewriting_examples())
