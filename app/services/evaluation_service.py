"""Evaluation service for golden test set"""
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
import uuid
import math

from app.models.golden_test import GoldenTest, EvaluationRun, EvaluationResult
from app.services.retrieval_service import retrieval_service
from app.services.query_service import query_service
from app.core.logging import get_logger

logger = get_logger(__name__)


class EvaluationService:
    """Service for running and analyzing golden test evaluations"""

    async def create_golden_test(
        self,
        query: str,
        expected_message_ids: List[str],
        db: AsyncSession,
        test_name: str = None,
        query_type: str = None,
        expected_answer: str = None,
        category: str = None,
        difficulty: str = "medium",
        min_relevance_score: float = 0.7,
        required_in_top_k: int = 5
    ) -> str:
        """Create a new golden test case"""
        test_id = str(uuid.uuid4())

        golden_test = GoldenTest(
            test_id=test_id,
            test_name=test_name or f"Test: {query[:50]}",
            query=query,
            query_type=query_type,
            expected_message_ids=expected_message_ids,
            expected_answer=expected_answer,
            category=category,
            difficulty=difficulty,
            min_relevance_score=min_relevance_score,
            required_in_top_k=required_in_top_k,
            is_active=1
        )

        db.add(golden_test)
        await db.commit()

        logger.info("golden_test_created", test_id=test_id, query=query)
        return test_id

    async def run_evaluation(
        self,
        db: AsyncSession,
        user_id: str = "eval_system",
        run_name: str = None,
        model_version: str = None,
        category_filter: str = None,
        top_k: int = 10
    ) -> str:
        """Run evaluation against all active golden tests"""
        run_id = str(uuid.uuid4())

        logger.info("evaluation_run_started", run_id=run_id, run_name=run_name)

        # Get all active tests
        query = select(GoldenTest).where(GoldenTest.is_active == 1)
        if category_filter:
            query = query.where(GoldenTest.category == category_filter)

        result = await db.execute(query)
        tests = result.scalars().all()

        if not tests:
            logger.warning("no_active_tests_found")
            return None

        # Create evaluation run
        eval_run = EvaluationRun(
            run_id=run_id,
            run_name=run_name or f"Evaluation {datetime.utcnow().isoformat()}",
            model_version=model_version,
            total_tests=len(tests),
            started_at=datetime.utcnow()
        )
        db.add(eval_run)
        await db.commit()

        # Run each test
        passed = 0
        failed = 0
        all_metrics = {
            "precision_at_5": [],
            "recall_at_5": [],
            "mrr": [],
            "ndcg_at_5": []
        }

        for test in tests:
            result = await self._evaluate_single_test(
                test=test,
                run_id=run_id,
                user_id=user_id,
                top_k=top_k,
                db=db
            )

            if result["passed"]:
                passed += 1
            else:
                failed += 1

            # Collect metrics
            for metric in all_metrics.keys():
                if result.get(metric) is not None:
                    all_metrics[metric].append(result[metric])

        # Update run with results
        eval_run.passed_tests = passed
        eval_run.failed_tests = failed
        eval_run.avg_precision_at_5 = self._avg(all_metrics["precision_at_5"])
        eval_run.avg_recall_at_5 = self._avg(all_metrics["recall_at_5"])
        eval_run.avg_mrr = self._avg(all_metrics["mrr"])
        eval_run.avg_ndcg = self._avg(all_metrics["ndcg_at_5"])
        eval_run.completed_at = datetime.utcnow()

        await db.commit()

        logger.info(
            "evaluation_run_completed",
            run_id=run_id,
            total=len(tests),
            passed=passed,
            failed=failed,
            avg_p5=eval_run.avg_precision_at_5,
            avg_mrr=eval_run.avg_mrr
        )

        return run_id

    async def _evaluate_single_test(
        self,
        test: GoldenTest,
        run_id: str,
        user_id: str,
        top_k: int,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Evaluate a single test case"""
        try:
            # Analyze query
            analysis = await query_service.analyze_query(test.query)

            # Retrieve results
            retrieval_results = await retrieval_service.retrieve(
                query=test.query,
                query_analysis=analysis,
                user_id=user_id,
                db=db,
                top_k=top_k
            )

            # Extract retrieved message IDs
            retrieved_ids = [r.get("message_id") for r in retrieval_results if r.get("message_id")]
            scores = [r.get("score", 0) for r in retrieval_results]

            # Calculate metrics
            expected_ids = set(test.expected_message_ids or [])
            metrics = self._calculate_metrics(
                retrieved_ids=retrieved_ids,
                expected_ids=expected_ids,
                scores=scores,
                k=min(test.required_in_top_k, 5)
            )

            # Determine pass/fail
            passed = self._check_pass_criteria(
                metrics=metrics,
                test=test,
                retrieved_ids=retrieved_ids
            )

            # Save result
            eval_result = EvaluationResult(
                run_id=run_id,
                test_id=test.test_id,
                query=test.query,
                retrieved_message_ids=retrieved_ids,
                retrieval_scores=scores,
                precision_at_5=metrics["precision_at_5"],
                recall_at_5=metrics["recall_at_5"],
                reciprocal_rank=metrics["reciprocal_rank"],
                ndcg_at_5=metrics["ndcg_at_5"],
                passed=1 if passed else 0,
                failure_reason=metrics.get("failure_reason")
            )

            db.add(eval_result)
            await db.commit()

            return {
                "passed": passed,
                "precision_at_5": metrics["precision_at_5"],
                "recall_at_5": metrics["recall_at_5"],
                "mrr": metrics["reciprocal_rank"],
                "ndcg_at_5": metrics["ndcg_at_5"]
            }

        except Exception as e:
            logger.error("evaluation_test_error", test_id=test.test_id, error=str(e))
            return {
                "passed": False,
                "precision_at_5": 0,
                "recall_at_5": 0,
                "mrr": 0,
                "ndcg_at_5": 0
            }

    def _calculate_metrics(
        self,
        retrieved_ids: List[str],
        expected_ids: set,
        scores: List[float],
        k: int = 5
    ) -> Dict[str, float]:
        """Calculate retrieval metrics"""
        # Limit to top K
        top_k_ids = retrieved_ids[:k]
        top_k_scores = scores[:k]

        # Precision@K: proportion of retrieved docs that are relevant
        relevant_retrieved = len([id for id in top_k_ids if id in expected_ids])
        precision_at_k = relevant_retrieved / k if k > 0 else 0

        # Recall@K: proportion of relevant docs that were retrieved
        recall_at_k = relevant_retrieved / len(expected_ids) if len(expected_ids) > 0 else 0

        # MRR (Mean Reciprocal Rank): 1/rank of first relevant document
        reciprocal_rank = 0
        for idx, doc_id in enumerate(retrieved_ids, 1):
            if doc_id in expected_ids:
                reciprocal_rank = 1.0 / idx
                break

        # NDCG@K (Normalized Discounted Cumulative Gain)
        ndcg = self._calculate_ndcg(retrieved_ids[:k], expected_ids, top_k_scores)

        return {
            "precision_at_5": precision_at_k,
            "recall_at_5": recall_at_k,
            "reciprocal_rank": reciprocal_rank,
            "ndcg_at_5": ndcg
        }

    def _calculate_ndcg(
        self,
        retrieved_ids: List[str],
        expected_ids: set,
        scores: List[float]
    ) -> float:
        """Calculate Normalized Discounted Cumulative Gain"""
        if not retrieved_ids:
            return 0.0

        # DCG: sum of (relevance / log2(position + 1))
        dcg = 0.0
        for idx, doc_id in enumerate(retrieved_ids):
            relevance = 1 if doc_id in expected_ids else 0
            dcg += relevance / math.log2(idx + 2)  # +2 because idx starts at 0

        # IDCG: ideal DCG (all relevant docs at top)
        ideal_relevances = [1] * min(len(expected_ids), len(retrieved_ids))
        idcg = sum(rel / math.log2(idx + 2) for idx, rel in enumerate(ideal_relevances))

        return dcg / idcg if idcg > 0 else 0.0

    def _check_pass_criteria(
        self,
        metrics: Dict[str, float],
        test: GoldenTest,
        retrieved_ids: List[str]
    ) -> bool:
        """Check if test passes based on criteria"""
        # Check if required messages appear in top K
        top_k_ids = set(retrieved_ids[:test.required_in_top_k])
        expected_ids = set(test.expected_message_ids or [])

        if not expected_ids.issubset(top_k_ids):
            metrics["failure_reason"] = f"Not all expected messages in top {test.required_in_top_k}"
            return False

        # Check minimum relevance score
        if metrics["precision_at_5"] < test.min_relevance_score:
            metrics["failure_reason"] = f"Precision {metrics['precision_at_5']:.2f} < {test.min_relevance_score}"
            return False

        return True

    def _avg(self, values: List[float]) -> Optional[float]:
        """Calculate average, handling empty lists"""
        return sum(values) / len(values) if values else None

    async def get_evaluation_summary(
        self,
        run_id: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Get summary of evaluation run"""
        # Get run
        result = await db.execute(
            select(EvaluationRun).where(EvaluationRun.run_id == run_id)
        )
        run = result.scalar_one_or_none()

        if not run:
            return None

        # Get all results
        result = await db.execute(
            select(EvaluationResult).where(EvaluationResult.run_id == run_id)
        )
        results = result.scalars().all()

        # Group by category
        category_stats = {}
        for res in results:
            # Get test category
            test_result = await db.execute(
                select(GoldenTest).where(GoldenTest.test_id == res.test_id)
            )
            test = test_result.scalar_one_or_none()

            if test and test.category:
                if test.category not in category_stats:
                    category_stats[test.category] = {"passed": 0, "failed": 0}

                if res.passed:
                    category_stats[test.category]["passed"] += 1
                else:
                    category_stats[test.category]["failed"] += 1

        return {
            "run_id": run.run_id,
            "run_name": run.run_name,
            "total_tests": run.total_tests,
            "passed_tests": run.passed_tests,
            "failed_tests": run.failed_tests,
            "pass_rate": run.passed_tests / run.total_tests if run.total_tests > 0 else 0,
            "avg_precision_at_5": run.avg_precision_at_5,
            "avg_recall_at_5": run.avg_recall_at_5,
            "avg_mrr": run.avg_mrr,
            "avg_ndcg": run.avg_ndcg,
            "category_stats": category_stats,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None
        }


# Global evaluation service instance
evaluation_service = EvaluationService()
