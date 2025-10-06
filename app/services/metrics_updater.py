"""Background service to update aggregate Prometheus metrics"""
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, func
from app.core.database import db_manager
from app.models.query_log import QueryLog
from app.models.golden_test import EvaluationRun
from app.core.monitoring import (
    feedback_rate,
    avg_retrieval_score,
    query_rewrite_rate,
    query_expansion_rate,
    evaluation_pass_rate,
    evaluation_precision,
    evaluation_recall,
    evaluation_mrr,
    evaluation_ndcg
)
from app.core.logging import get_logger

logger = get_logger(__name__)


class MetricsUpdater:
    """Updates aggregate metrics for Prometheus"""

    async def update_all_metrics(self):
        """Update all aggregate metrics"""
        async for session in db_manager.get_session():
            try:
                await self._update_feedback_rate(session)
                await self._update_avg_retrieval_score(session)
                await self._update_query_enhancement_rates(session)
                await self._update_evaluation_metrics(session)

                logger.info("metrics_updated")

            except Exception as e:
                logger.error("metrics_update_error", error=str(e))

    async def _update_feedback_rate(self, session):
        """Calculate and update feedback rate"""
        # Count queries in last 24h
        since = datetime.utcnow() - timedelta(hours=24)

        total_result = await session.execute(
            select(func.count(QueryLog.id))
            .where(QueryLog.created_at >= since)
        )
        total = total_result.scalar() or 0

        # Count queries with feedback
        feedback_result = await session.execute(
            select(func.count(QueryLog.id))
            .where(QueryLog.created_at >= since)
            .where(QueryLog.user_rating.isnot(None))
        )
        with_feedback = feedback_result.scalar() or 0

        # Update gauge
        rate = (with_feedback / total * 100) if total > 0 else 0
        feedback_rate.set(rate)

    async def _update_avg_retrieval_score(self, session):
        """Calculate average retrieval scores for different time windows"""
        windows = {
            "1h": timedelta(hours=1),
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7)
        }

        for window_name, delta in windows.items():
            since = datetime.utcnow() - delta

            result = await session.execute(
                select(func.avg(QueryLog.top_result_score))
                .where(QueryLog.created_at >= since)
                .where(QueryLog.top_result_score.isnot(None))
            )
            avg_score = result.scalar() or 0

            avg_retrieval_score.labels(time_window=window_name).set(avg_score)

    async def _update_query_enhancement_rates(self, session):
        """Calculate query rewriting and expansion rates"""
        since = datetime.utcnow() - timedelta(hours=24)

        # Total queries
        total_result = await session.execute(
            select(func.count(QueryLog.id))
            .where(QueryLog.created_at >= since)
        )
        total = total_result.scalar() or 0

        if total == 0:
            query_rewrite_rate.set(0)
            query_expansion_rate.set(0)
            return

        # Rewritten queries
        rewrite_result = await session.execute(
            select(func.count(QueryLog.id))
            .where(QueryLog.created_at >= since)
            .where(QueryLog.query_was_rewritten == 1)
        )
        rewritten = rewrite_result.scalar() or 0

        # Expanded queries
        expand_result = await session.execute(
            select(func.count(QueryLog.id))
            .where(QueryLog.created_at >= since)
            .where(QueryLog.query_was_expanded == 1)
        )
        expanded = expand_result.scalar() or 0

        query_rewrite_rate.set((rewritten / total * 100))
        query_expansion_rate.set((expanded / total * 100))

    async def _update_evaluation_metrics(self, session):
        """Update metrics from latest evaluation run"""
        # Get most recent completed evaluation run
        result = await session.execute(
            select(EvaluationRun)
            .where(EvaluationRun.completed_at.isnot(None))
            .order_by(EvaluationRun.completed_at.desc())
            .limit(1)
        )
        latest_run = result.scalar_one_or_none()

        if not latest_run:
            return

        # Overall metrics
        pass_rate = (latest_run.passed_tests / latest_run.total_tests * 100) if latest_run.total_tests > 0 else 0

        evaluation_pass_rate.labels(category="overall").set(pass_rate)

        if latest_run.avg_precision_at_5:
            evaluation_precision.labels(category="overall").set(latest_run.avg_precision_at_5)
        if latest_run.avg_recall_at_5:
            evaluation_recall.labels(category="overall").set(latest_run.avg_recall_at_5)
        if latest_run.avg_mrr:
            evaluation_mrr.labels(category="overall").set(latest_run.avg_mrr)
        if latest_run.avg_ndcg:
            evaluation_ndcg.labels(category="overall").set(latest_run.avg_ndcg)

        # Per-category metrics (if needed, query evaluation_results grouped by test category)
        # This would require joining with golden_tests table


# Global instance
metrics_updater = MetricsUpdater()


async def metrics_update_loop():
    """Background loop to update metrics every 60 seconds"""
    db_manager.initialize()

    while True:
        try:
            await metrics_updater.update_all_metrics()
        except Exception as e:
            logger.error("metrics_loop_error", error=str(e))

        await asyncio.sleep(60)  # Update every minute


# Run as background task in main app
async def start_metrics_updater():
    """Start the metrics updater background task"""
    asyncio.create_task(metrics_update_loop())
    logger.info("metrics_updater_started")
