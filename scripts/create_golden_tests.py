"""Create golden test cases from query logs or manually"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import db_manager
from app.services.evaluation_service import evaluation_service
from app.core.logging import get_logger

logger = get_logger(__name__)


async def create_test_from_query_log():
    """Interactive: Create golden test from existing query log"""
    db_manager.initialize()

    async for session in db_manager.get_session():
        from app.models.query_log import QueryLog
        from sqlalchemy import select, desc

        # Show recent queries with good ratings
        print("\n" + "="*60)
        print("RECENT HIGHLY-RATED QUERIES")
        print("="*60)

        result = await session.execute(
            select(QueryLog)
            .where(QueryLog.user_rating >= 4)
            .order_by(desc(QueryLog.created_at))
            .limit(20)
        )
        queries = result.scalars().all()

        if not queries:
            print("No highly-rated queries found yet.")
            print("Try rating some bot responses first using the feedback buttons.")
            return

        for idx, q in enumerate(queries, 1):
            print(f"\n{idx}. Query: {q.query_text[:80]}...")
            print(f"   Rating: {q.user_rating}/5")
            print(f"   Results: {q.results_count}")
            print(f"   Query ID: {q.query_id}")

        # Get user selection
        selection = input(f"\nSelect query (1-{len(queries)}) or 0 to cancel: ")

        try:
            idx = int(selection)
            if idx == 0:
                print("Cancelled.")
                return

            selected_query = queries[idx - 1]
        except (ValueError, IndexError):
            print("Invalid selection.")
            return

        # Show query details
        print("\n" + "="*60)
        print("CREATE GOLDEN TEST")
        print("="*60)
        print(f"Query: {selected_query.query_text}")
        print(f"Results returned: {selected_query.results_count}")
        print(f"Message IDs: {selected_query.result_message_ids}")

        # Get test parameters
        test_name = input("\nTest name (or press Enter for auto): ").strip()
        if not test_name:
            test_name = f"Test: {selected_query.query_text[:50]}"

        category = input("Category (engineering/product/support/other): ").strip() or "other"
        difficulty = input("Difficulty (easy/medium/hard) [medium]: ").strip() or "medium"

        min_score = input("Min relevance score (0-1) [0.7]: ").strip()
        min_score = float(min_score) if min_score else 0.7

        top_k = input("Must appear in top K [5]: ").strip()
        top_k = int(top_k) if top_k else 5

        # Create golden test
        test_id = await evaluation_service.create_golden_test(
            query=selected_query.query_text,
            expected_message_ids=selected_query.result_message_ids or [],
            test_name=test_name,
            query_type=selected_query.intent_type,
            category=category,
            difficulty=difficulty,
            min_relevance_score=min_score,
            required_in_top_k=top_k,
            db=session
        )

        print(f"\n✓ Golden test created: {test_id}")

    await db_manager.close()


async def create_manual_test():
    """Interactive: Create golden test manually"""
    db_manager.initialize()

    async for session in db_manager.get_session():
        print("\n" + "="*60)
        print("CREATE MANUAL GOLDEN TEST")
        print("="*60)

        # Get test parameters
        query = input("\nQuery text: ").strip()
        if not query:
            print("Query is required.")
            return

        test_name = input("Test name (or press Enter for auto): ").strip()
        if not test_name:
            test_name = f"Test: {query[:50]}"

        # Get expected message IDs
        print("\nExpected message IDs (comma-separated):")
        print("Example: 1234567.890123, 1234567.890456")
        message_ids_input = input("> ").strip()

        if not message_ids_input:
            print("At least one expected message ID is required.")
            return

        expected_message_ids = [mid.strip() for mid in message_ids_input.split(",")]

        category = input("Category (engineering/product/support/other): ").strip() or "other"
        query_type = input("Query type (factual/code/summary/timeline/other): ").strip() or None
        difficulty = input("Difficulty (easy/medium/hard) [medium]: ").strip() or "medium"

        min_score = input("Min relevance score (0-1) [0.7]: ").strip()
        min_score = float(min_score) if min_score else 0.7

        top_k = input("Must appear in top K [5]: ").strip()
        top_k = int(top_k) if top_k else 5

        # Create golden test
        test_id = await evaluation_service.create_golden_test(
            query=query,
            expected_message_ids=expected_message_ids,
            test_name=test_name,
            query_type=query_type,
            category=category,
            difficulty=difficulty,
            min_relevance_score=min_score,
            required_in_top_k=top_k,
            db=session
        )

        print(f"\n✓ Golden test created: {test_id}")

    await db_manager.close()


async def list_golden_tests():
    """List all golden tests"""
    db_manager.initialize()

    async for session in db_manager.get_session():
        from app.models.golden_test import GoldenTest
        from sqlalchemy import select, func

        # Count by category
        result = await session.execute(
            select(
                GoldenTest.category,
                GoldenTest.is_active,
                func.count(GoldenTest.id).label('count')
            )
            .group_by(GoldenTest.category, GoldenTest.is_active)
        )
        stats = result.all()

        print("\n" + "="*60)
        print("GOLDEN TESTS SUMMARY")
        print("="*60)

        for category, is_active, count in stats:
            status = "Active" if is_active else "Inactive"
            print(f"{category or 'Uncategorized'} ({status}): {count} tests")

        # Get all tests
        result = await session.execute(
            select(GoldenTest).order_by(GoldenTest.created_at.desc())
        )
        tests = result.scalars().all()

        if tests:
            print("\n" + "="*60)
            print("ALL GOLDEN TESTS")
            print("="*60)

            for test in tests:
                status = "✓ Active" if test.is_active else "✗ Inactive"
                print(f"\n{status} | {test.category or 'other'} | {test.difficulty}")
                print(f"  Name: {test.test_name}")
                print(f"  Query: {test.query[:80]}...")
                print(f"  Expected: {len(test.expected_message_ids or [])} messages in top {test.required_in_top_k}")
                print(f"  Min score: {test.min_relevance_score}")
                print(f"  ID: {test.test_id}")

    await db_manager.close()


async def run_evaluation():
    """Run evaluation against all active golden tests"""
    db_manager.initialize()

    async for session in db_manager.get_session():
        from app.models.golden_test import GoldenTest
        from sqlalchemy import select, func

        # Count active tests
        result = await session.execute(
            select(func.count(GoldenTest.id))
            .where(GoldenTest.is_active == 1)
        )
        count = result.scalar()

        if count == 0:
            print("No active golden tests found.")
            print("Create some tests first using option 1 or 2.")
            return

        print("\n" + "="*60)
        print("RUN EVALUATION")
        print("="*60)
        print(f"\nFound {count} active golden tests")

        run_name = input("\nEvaluation run name (or press Enter for auto): ").strip()
        if not run_name:
            from datetime import datetime
            run_name = f"Evaluation {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"

        category = input("Filter by category (or press Enter for all): ").strip() or None

        print(f"\n⏳ Running evaluation: {run_name}")
        print("This may take a few minutes...")

        # Run evaluation
        run_id = await evaluation_service.run_evaluation(
            db=session,
            run_name=run_name,
            category_filter=category,
            top_k=10
        )

        if not run_id:
            print("✗ No tests to run")
            return

        # Get results
        summary = await evaluation_service.get_evaluation_summary(run_id, session)

        print("\n" + "="*60)
        print("EVALUATION RESULTS")
        print("="*60)
        print(f"\nRun: {summary['run_name']}")
        print(f"Run ID: {summary['run_id']}")
        print(f"\nTests: {summary['total_tests']}")
        print(f"  ✓ Passed: {summary['passed_tests']}")
        print(f"  ✗ Failed: {summary['failed_tests']}")
        print(f"  Pass rate: {summary['pass_rate']*100:.1f}%")

        print(f"\nMetrics:")
        print(f"  Precision@5: {summary['avg_precision_at_5']:.3f}" if summary['avg_precision_at_5'] else "  Precision@5: N/A")
        print(f"  Recall@5: {summary['avg_recall_at_5']:.3f}" if summary['avg_recall_at_5'] else "  Recall@5: N/A")
        print(f"  MRR: {summary['avg_mrr']:.3f}" if summary['avg_mrr'] else "  MRR: N/A")
        print(f"  NDCG@5: {summary['avg_ndcg']:.3f}" if summary['avg_ndcg'] else "  NDCG@5: N/A")

        if summary['category_stats']:
            print(f"\nBy Category:")
            for cat, stats in summary['category_stats'].items():
                total = stats['passed'] + stats['failed']
                rate = (stats['passed'] / total * 100) if total > 0 else 0
                print(f"  {cat}: {stats['passed']}/{total} passed ({rate:.1f}%)")

        print(f"\nStarted: {summary['started_at']}")
        print(f"Completed: {summary['completed_at']}")

    await db_manager.close()


async def main():
    """Main menu"""
    while True:
        print("\n" + "="*60)
        print("GOLDEN TEST SET MANAGER")
        print("="*60)
        print("\n1. Create test from existing query log")
        print("2. Create test manually")
        print("3. List all golden tests")
        print("4. Run evaluation")
        print("5. Exit")

        choice = input("\nSelect option (1-5): ").strip()

        if choice == "1":
            await create_test_from_query_log()
        elif choice == "2":
            await create_manual_test()
        elif choice == "3":
            await list_golden_tests()
        elif choice == "4":
            await run_evaluation()
        elif choice == "5":
            print("Goodbye!")
            break
        else:
            print("Invalid choice. Please select 1-5.")


if __name__ == "__main__":
    asyncio.run(main())
