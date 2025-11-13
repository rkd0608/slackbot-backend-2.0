#!/usr/bin/env python3
"""
Test Team Vocabulary Learning Service

Tests:
1. Seeding common abbreviations
2. Learning from explicit patterns: "k8s (Kubernetes)"
3. Learning from co-occurrence
4. Query expansion with learned vocabulary
5. Confidence scoring
6. Disabling incorrect terms
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db_manager
from app.services.team_vocabulary_service import TeamVocabularyService

# Initialize database
db_manager.initialize()


async def test_common_abbreviations():
    """Test seeding common abbreviations"""
    print("=" * 80)
    print("TEST 1: SEEDING COMMON ABBREVIATIONS")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        service = TeamVocabularyService(db)

        team_id = "T_TEST_VOCAB"

        print(f"\nSeeding common abbreviations for team: {team_id}")
        seeded_count = await service.seed_common_abbreviations(team_id)

        print(f"✓ Seeded {seeded_count} common abbreviations")

        # Get vocabulary
        vocab = await service.get_team_vocabulary(team_id, limit=20)
        print(f"\nLearned vocabulary ({len(vocab)} terms):")
        for item in vocab[:10]:
            print(f"  {item['term']:<15} -> {item['canonical_form']:<30} (confidence: {item['confidence_score']:.2f})")

    return seeded_count


async def test_explicit_patterns():
    """Test learning from explicit patterns"""
    print("\n" + "=" * 80)
    print("TEST 2: LEARNING FROM EXPLICIT PATTERNS")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        service = TeamVocabularyService(db)

        team_id = "T_TEST_VOCAB"

        test_messages = [
            "We use k8s (Kubernetes) for container orchestration",
            "The FE (frontend) team is working on the dashboard",
            "Check the DB (database) migrations before deploying",
            "Our API (Application Programming Interface) handles authentication",
            "The prod (production) environment is down"
        ]

        print("\nLearning from messages:")
        for msg in test_messages:
            print(f"\nMessage: \"{msg}\"")
            learned = await service.learn_from_message(team_id, msg)
            if learned:
                for term in learned:
                    print(f"  ✓ Learned: {term['term']} -> {term['canonical_form']}")

        # Get updated vocabulary
        vocab = await service.get_team_vocabulary(team_id)
        print(f"\n\nTotal vocabulary: {len(vocab)} terms")


async def test_cooccurrence():
    """Test learning from co-occurrence"""
    print("\n" + "=" * 80)
    print("TEST 3: LEARNING FROM CO-OCCURRENCE")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        service = TeamVocabularyService(db)

        team_id = "T_TEST_VOCAB"

        test_cases = [
            ("k8s", "kubernetes", 5),
            ("db", "database", 10),
            ("svc", "service", 3),
            ("cfg", "configuration", 2),
        ]

        print("\nLearning from co-occurrence:")
        for short, long, count in test_cases:
            print(f"\n{short} appears with {long} ({count} times)")
            learned = await service.learn_from_cooccurrence(
                team_id=team_id,
                short_term=short,
                long_term=long,
                cooccurrence_count=count
            )
            if learned:
                print(f"  ✓ Learned mapping")
            else:
                print(f"  ✗ Rejected (low confidence or invalid)")


async def test_query_expansion():
    """Test query expansion with learned vocabulary"""
    print("\n" + "=" * 80)
    print("TEST 4: QUERY EXPANSION")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        service = TeamVocabularyService(db)

        team_id = "T_TEST_VOCAB"

        # Ensure we have vocabulary
        await service.seed_common_abbreviations(team_id)

        test_queries = [
            "k8s deployment failed",
            "FE bug in the UI",
            "db migration issues",
            "api authentication problems",
            "prod deployment with ci/cd",
            "How do I configure the database?"  # Should expand 'db' if learned
        ]

        print("\nExpanding queries:")
        for query in test_queries:
            result = await service.expand_query(team_id, query)

            print(f"\nOriginal:  \"{result['original_query']}\"")
            print(f"Expanded:  \"{result['expanded_query']}\"")

            if result['expansions']:
                print("Expansions:")
                for exp in result['expansions']:
                    print(f"  - {exp['term']} -> {exp['canonical_form']} (confidence: {exp['confidence']:.2f})")
            else:
                print("No expansions applied")


async def test_confidence_calculation():
    """Test abbreviation confidence calculation"""
    print("\n" + "=" * 80)
    print("TEST 5: CONFIDENCE CALCULATION")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        service = TeamVocabularyService(db)

        test_cases = [
            ("k8s", "kubernetes", "Numeronym pattern"),
            ("api", "application programming interface", "Initial letters match"),
            ("db", "database", "Initial letters match"),
            ("ui", "user interface", "Initial letters match"),
            ("xyz", "something", "No match - should be low"),
            ("prod", "production", "Partial match"),
        ]

        print("\nCalculating confidence scores:")
        print(f"{'Short':<10} {'Long':<35} {'Score':<8} Reason")
        print("-" * 80)

        for short, long, reason in test_cases:
            score = service._calculate_abbreviation_confidence(short, long)
            print(f"{short:<10} {long:<35} {score:<8.2f} {reason}")


async def test_disable_term():
    """Test disabling incorrect terms"""
    print("\n" + "=" * 80)
    print("TEST 6: DISABLING INCORRECT TERMS")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        service = TeamVocabularyService(db)

        team_id = "T_TEST_VOCAB"

        # Add a test term
        await service.learn_from_cooccurrence(
            team_id=team_id,
            short_term="test",
            long_term="testing",
            cooccurrence_count=1
        )

        print("\nVocabulary before disabling:")
        vocab = await service.get_team_vocabulary(team_id, limit=5)
        for item in vocab:
            if item['term'] == 'test':
                print(f"  ✓ Found: {item['term']} -> {item['canonical_form']}")

        # Disable the term
        print("\nDisabling term 'test'...")
        disabled = await service.disable_term(team_id, "test")
        print(f"  {'✓' if disabled else '✗'} Term disabled")

        # Verify it's not in active vocabulary
        print("\nVocabulary after disabling:")
        vocab_after = await service.get_team_vocabulary(team_id, limit=100)
        found = any(item['term'] == 'test' for item in vocab_after)
        if not found:
            print("  ✓ Term no longer in active vocabulary")
        else:
            print("  ✗ Term still in active vocabulary (unexpected)")


async def test_full_workflow():
    """Test complete workflow: learn -> expand -> verify"""
    print("\n" + "=" * 80)
    print("TEST 7: FULL WORKFLOW")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        service = TeamVocabularyService(db)

        team_id = "T_WORKFLOW_TEST"

        print("\nStep 1: Seed common abbreviations")
        seeded = await service.seed_common_abbreviations(team_id)
        print(f"  ✓ Seeded {seeded} terms")

        print("\nStep 2: Learn from team messages")
        messages = [
            "We deployed to prod using k8s",
            "The FE needs to call the API",
            "Check the DB before running migrations"
        ]
        for msg in messages:
            learned = await service.learn_from_message(team_id, msg)
            print(f"  Message: \"{msg}\"")
            print(f"  Learned: {len(learned)} terms")

        print("\nStep 3: Get team vocabulary")
        vocab = await service.get_team_vocabulary(team_id)
        print(f"  ✓ Team has {len(vocab)} terms")
        print("\n  Top 10 terms:")
        for item in vocab[:10]:
            print(f"    {item['term']:<10} -> {item['canonical_form']:<30} "
                  f"(used {item['occurrence_count']}x, confidence: {item['confidence_score']:.2f})")

        print("\nStep 4: Expand user queries")
        queries = [
            "k8s prod issues",
            "FE API integration",
            "db migration"
        ]
        for query in queries:
            result = await service.expand_query(team_id, query)
            print(f"\n  Query: \"{query}\"")
            print(f"  Expanded: \"{result['expanded_query']}\"")
            if result['expansions']:
                print(f"  Applied {len(result['expansions'])} expansions")


async def run_all_tests():
    """Run all tests"""
    print("\n" + "=" * 80)
    print("TEAM VOCABULARY LEARNING SERVICE - COMPREHENSIVE TESTS")
    print("=" * 80)

    try:
        await test_common_abbreviations()
        await test_explicit_patterns()
        await test_cooccurrence()
        await test_query_expansion()
        await test_confidence_calculation()
        await test_disable_term()
        await test_full_workflow()

        print("\n" + "=" * 80)
        print("ALL TESTS COMPLETED")
        print("=" * 80)

    except Exception as e:
        print(f"\n✗ Error during testing: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_all_tests())
