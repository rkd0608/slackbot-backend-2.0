"""
Test Query Intelligence Features

Demonstrates:
1. Query Decomposition
2. Learned Rewrites
3. Improvement Suggestions
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import db_manager
from app.services.query_decomposer import query_decomposer
from app.services.query_rewrite_learner import get_rewrite_learner
from app.core.logging import get_logger

logger = get_logger(__name__)


async def test_decomposition():
    """Test query decomposition"""
    print("\n" + "="*60)
    print("TEST 1: Query Decomposition")
    print("="*60)

    test_queries = [
        "Show me PRs and discussions about authentication",
        "What did John say about deployment and what code changes were made?",
        "Find issues related to performance in the API",
        "Tell me about database migration"  # Simple query - should not decompose
    ]

    for query in test_queries:
        print(f"\nQuery: {query}")
        result = await query_decomposer.decompose_query(query)

        if result['needs_decomposition']:
            print(f"  ✓ Needs decomposition: {result.get('decomposition_reason')}")
            print(f"  Sub-queries ({len(result['sub_queries'])}):")
            for i, sq in enumerate(result['sub_queries'], 1):
                print(f"    {i}. [{sq.get('weight', 1.0):.2f}] {sq['query']}")
                print(f"       Strategy: {sq.get('search_strategy')}, Source: {sq.get('source_preference')}")
        else:
            print(f"  ✗ No decomposition needed")


async def test_learned_rewrites():
    """Test learned rewrite application"""
    print("\n" + "="*60)
    print("TEST 2: Learned Rewrites")
    print("="*60)

    team_id = "T0420EE1VQ8"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            learner = get_rewrite_learner(db)

            # Check for any existing rules
            from app.models.learning_signal import QueryRewriteRule
            from sqlalchemy import select

            result = await db.execute(
                select(QueryRewriteRule).where(
                    QueryRewriteRule.team_id == team_id
                ).limit(5)
            )
            rules = result.scalars().all()

            print(f"\nExisting rewrite rules: {len(rules)}")
            for rule in rules:
                print(f"  - {rule.rule_type}: {rule.trigger_pattern}")
                print(f"    Improvement rate: {rule.improvement_rate:.2f}")
                print(f"    Applications: {rule.applications_count}")

            # Test applying rewrites
            test_query = "show me info about db migration"
            print(f"\nTest query: {test_query}")

            rewrite_result = await learner.apply_learned_rewrites(
                query=test_query,
                team_id=team_id
            )

            print(f"Rewritten: {rewrite_result['rewritten_query']}")
            print(f"Rules applied: {len(rewrite_result['rules_applied'])}")

            # Test suggestions
            suggestions = await learner.get_improvement_suggestions(
                query=test_query,
                team_id=team_id
            )

            print(f"\nImprovement suggestions: {len(suggestions)}")
            for i, sug in enumerate(suggestions[:3], 1):
                print(f"  {i}. [{sug.get('type')}] {sug.get('action')}")
                print(f"     {sug.get('details')}")
                print(f"     Confidence: {sug.get('confidence', 0):.2f}")

        except Exception as e:
            logger.error("test_learned_rewrites_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def test_query_patterns():
    """Test query pattern learning"""
    print("\n" + "="*60)
    print("TEST 3: Query Patterns")
    print("="*60)

    team_id = "T0420EE1VQ8"

    db_manager.initialize()

    async for db in db_manager.get_session():
        try:
            from app.models.learning_signal import SuccessfulQueryPattern, FailedQueryPattern
            from sqlalchemy import select, func

            # Check successful patterns
            success_count = await db.execute(
                select(func.count(SuccessfulQueryPattern.id)).where(
                    SuccessfulQueryPattern.team_id == team_id
                )
            )
            success_total = success_count.scalar()

            # Check failed patterns
            failure_count = await db.execute(
                select(func.count(FailedQueryPattern.id)).where(
                    FailedQueryPattern.team_id == team_id
                )
            )
            failure_total = failure_count.scalar()

            print(f"\nLearned Patterns:")
            print(f"  Successful patterns: {success_total}")
            print(f"  Failed patterns: {failure_total}")

            # Show some examples
            if success_total > 0:
                result = await db.execute(
                    select(SuccessfulQueryPattern).where(
                        SuccessfulQueryPattern.team_id == team_id
                    ).limit(3)
                )
                patterns = result.scalars().all()

                print(f"\nExample successful patterns:")
                for p in patterns:
                    print(f"  - {p.pattern_type} (success rate: {p.success_rate:.2f})")
                    if p.query_examples:
                        print(f"    Example: {p.query_examples[0]}")

        except Exception as e:
            logger.error("test_query_patterns_failed", error=str(e), exc_info=True)
            print(f"\n❌ FAILED: {str(e)}")

        break


async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("QUERY INTELLIGENCE TEST SUITE")
    print("="*60)

    # Test 1: Decomposition (doesn't need DB)
    await test_decomposition()

    # Test 2: Learned rewrites (needs DB)
    await test_learned_rewrites()

    # Test 3: Query patterns (needs DB)
    await test_query_patterns()

    print("\n" + "="*60)
    print("✅ All tests completed")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
