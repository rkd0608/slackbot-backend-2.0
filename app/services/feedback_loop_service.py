"""
Feedback Loop Service - Collects and processes learning signals

This service:
1. Collects feedback from user interactions (thumbs up/down, clicks, reformulations)
2. Analyzes patterns in successful and failed queries
3. Generates improvement suggestions
4. Feeds learning signals to query rewriter and retrieval optimizer
"""
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc

from app.core.logging import get_logger
from app.models.learning_signal import (
    LearningSignal,
    SuccessfulQueryPattern,
    FailedQueryPattern,
    QueryRewriteRule
)
from app.models.query_log import QueryLog

logger = get_logger(__name__)


class FeedbackLoopService:
    """Collects feedback and learns from user interactions"""

    # Signal types
    SIGNAL_THUMBS_UP = "thumbs_up"
    SIGNAL_THUMBS_DOWN = "thumbs_down"
    SIGNAL_CLICK = "click"
    SIGNAL_REFORMULATION = "reformulation"
    SIGNAL_NO_RESULTS = "no_results"
    SIGNAL_LOW_CONFIDENCE = "low_confidence"
    SIGNAL_COPY = "copy"

    # Signal strengths
    STRENGTH_POSITIVE = 1.0
    STRENGTH_NEGATIVE = -1.0
    STRENGTH_NEUTRAL = 0.5

    async def record_feedback(
        self,
        query_id: str,
        user_id: str,
        team_id: str,
        feedback_type: str,  # 'thumbs_up' or 'thumbs_down'
        context: Optional[Dict[str, Any]] = None,
        db: AsyncSession = None
    ) -> bool:
        """
        Record explicit user feedback (thumbs up/down)

        Args:
            query_id: ID of the query being rated
            user_id: User who gave feedback
            team_id: Team/workspace ID
            feedback_type: 'thumbs_up' or 'thumbs_down'
            context: Additional context (comment, alternative query suggestion)
            db: Database session

        Returns:
            True if feedback recorded successfully
        """
        try:
            # Get the original query from query_logs
            query_log = await db.execute(
                select(QueryLog).where(QueryLog.query_id == query_id)
            )
            query_log = query_log.scalar_one_or_none()

            if not query_log:
                logger.error("query_not_found_for_feedback", query_id=query_id)
                return False

            # Determine signal strength
            signal_strength = (
                self.STRENGTH_POSITIVE if feedback_type == self.SIGNAL_THUMBS_UP
                else self.STRENGTH_NEGATIVE
            )

            # Create learning signal
            signal = LearningSignal(
                signal_id=str(uuid.uuid4()),
                team_id=team_id,
                user_id=user_id,
                query_id=query_id,
                query_text=query_log.query_text,
                signal_type=feedback_type,
                signal_strength=signal_strength,
                signal_context=context or {},
                retrieval_method=self._get_primary_retrieval_method(query_log),
                retrieval_score=query_log.top_result_score,
                results_count=query_log.results_count,
                query_intent=query_log.intent_type,
                query_entities=query_log.entities_extracted,
                learned_from=False
            )

            db.add(signal)

            # Update query_log with feedback
            query_log.user_rating = 5 if feedback_type == self.SIGNAL_THUMBS_UP else 1
            query_log.feedback_type = 'positive' if feedback_type == self.SIGNAL_THUMBS_UP else 'negative'

            await db.commit()

            logger.info(
                "feedback_recorded",
                query_id=query_id,
                feedback_type=feedback_type,
                team_id=team_id
            )

            # Asynchronously learn from this signal
            await self._process_feedback_signal(signal, query_log, db)

            return True

        except Exception as e:
            logger.error("record_feedback_error", error=str(e), query_id=query_id)
            await db.rollback()
            return False

    async def record_result_click(
        self,
        query_id: str,
        user_id: str,
        team_id: str,
        result_position: int,
        result_id: str,
        result_score: float,
        db: AsyncSession
    ) -> bool:
        """Record when user clicks on a search result"""
        try:
            query_log = await db.execute(
                select(QueryLog).where(QueryLog.query_id == query_id)
            )
            query_log = query_log.scalar_one_or_none()

            if not query_log:
                return False

            signal = LearningSignal(
                signal_id=str(uuid.uuid4()),
                team_id=team_id,
                user_id=user_id,
                query_id=query_id,
                query_text=query_log.query_text,
                signal_type=self.SIGNAL_CLICK,
                signal_strength=self._calculate_click_strength(result_position),
                signal_context={
                    "result_position": result_position,
                    "result_id": result_id,
                    "score": result_score
                },
                retrieval_method=self._get_primary_retrieval_method(query_log),
                query_intent=query_log.intent_type,
                learned_from=False
            )

            db.add(signal)
            await db.commit()

            logger.info(
                "result_click_recorded",
                query_id=query_id,
                position=result_position,
                team_id=team_id
            )

            return True

        except Exception as e:
            logger.error("record_click_error", error=str(e), query_id=query_id)
            return False

    async def record_query_reformulation(
        self,
        original_query_id: str,
        new_query_id: str,
        user_id: str,
        team_id: str,
        results_improved: bool,
        db: AsyncSession
    ) -> bool:
        """
        Record when user reformulates a query

        This is a strong signal - user wasn't satisfied with first results
        """
        try:
            original_query = await db.execute(
                select(QueryLog).where(QueryLog.query_id == original_query_id)
            )
            original_query = original_query.scalar_one_or_none()

            new_query = await db.execute(
                select(QueryLog).where(QueryLog.query_id == new_query_id)
            )
            new_query = new_query.scalar_one_or_none()

            if not original_query or not new_query:
                return False

            signal = LearningSignal(
                signal_id=str(uuid.uuid4()),
                team_id=team_id,
                user_id=user_id,
                query_id=original_query_id,
                query_text=original_query.query_text,
                signal_type=self.SIGNAL_REFORMULATION,
                signal_strength=self.STRENGTH_NEGATIVE,  # Original query wasn't good enough
                signal_context={
                    "original_query": original_query.query_text,
                    "new_query": new_query.query_text,
                    "results_improved": results_improved,
                    "new_query_id": new_query_id
                },
                query_intent=original_query.intent_type,
                learned_from=False
            )

            db.add(signal)
            await db.commit()

            # If reformulation improved results, learn the rewrite pattern
            if results_improved:
                await self._learn_rewrite_pattern(original_query, new_query, db)

            logger.info(
                "reformulation_recorded",
                original=original_query.query_text,
                new=new_query.query_text,
                improved=results_improved
            )

            return True

        except Exception as e:
            logger.error("record_reformulation_error", error=str(e))
            return False

    async def record_no_results_signal(
        self,
        query_id: str,
        user_id: str,
        team_id: str,
        query_text: str,
        query_analysis: Dict[str, Any],
        db: AsyncSession
    ) -> bool:
        """Record when a query returns no results"""
        try:
            signal = LearningSignal(
                signal_id=str(uuid.uuid4()),
                team_id=team_id,
                user_id=user_id,
                query_id=query_id,
                query_text=query_text,
                signal_type=self.SIGNAL_NO_RESULTS,
                signal_strength=self.STRENGTH_NEGATIVE,
                signal_context={
                    "reason": "no_results_found",
                    "query_analysis": query_analysis
                },
                results_count=0,
                query_intent=query_analysis.get("query_type"),
                query_entities=query_analysis.get("entities"),
                learned_from=False
            )

            db.add(signal)
            await db.commit()

            # Immediately process to generate suggestions
            await self._process_no_results_signal(signal, query_analysis, db)

            logger.info("no_results_signal_recorded", query_id=query_id, team_id=team_id)
            return True

        except Exception as e:
            logger.error("record_no_results_error", error=str(e))
            return False

    async def get_improvement_suggestions(
        self,
        query_text: str,
        team_id: str,
        db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """
        Get improvement suggestions for a query based on learned patterns

        Returns:
            List of suggestions with confidence scores
        """
        suggestions = []

        try:
            # Check if similar queries have failed before
            similar_failures = await db.execute(
                select(FailedQueryPattern)
                .where(
                    and_(
                        FailedQueryPattern.team_id == team_id,
                        FailedQueryPattern.failure_rate > 0.5
                    )
                )
                .order_by(desc(FailedQueryPattern.failure_count))
                .limit(5)
            )
            similar_failures = similar_failures.scalars().all()

            for pattern in similar_failures:
                if pattern.improvement_suggestions:
                    for suggestion in pattern.improvement_suggestions:
                        suggestions.append({
                            "type": suggestion.get("action"),
                            "details": suggestion.get("details"),
                            "confidence": 1.0 - pattern.failure_rate,
                            "based_on": f"{pattern.failure_count} similar queries"
                        })

            # Check for applicable rewrite rules
            active_rules = await db.execute(
                select(QueryRewriteRule)
                .where(
                    and_(
                        QueryRewriteRule.team_id == team_id,
                        QueryRewriteRule.is_active == True,
                        QueryRewriteRule.improvement_rate > 0.3
                    )
                )
                .order_by(desc(QueryRewriteRule.improvement_rate))
                .limit(3)
            )
            active_rules = active_rules.scalars().all()

            for rule in active_rules:
                suggestions.append({
                    "type": "query_rewrite",
                    "rule_type": rule.rule_type,
                    "template": rule.rewrite_template,
                    "confidence": rule.confidence,
                    "improvement_rate": rule.improvement_rate
                })

            return suggestions

        except Exception as e:
            logger.error("get_suggestions_error", error=str(e))
            return []

    async def analyze_team_performance(
        self,
        team_id: str,
        days: int = 7,
        db: AsyncSession = None
    ) -> Dict[str, Any]:
        """
        Analyze query performance for a team

        Returns:
            Performance metrics and insights
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            # Get signal counts
            signals = await db.execute(
                select(
                    LearningSignal.signal_type,
                    func.count(LearningSignal.id).label('count'),
                    func.avg(LearningSignal.signal_strength).label('avg_strength')
                )
                .where(
                    and_(
                        LearningSignal.team_id == team_id,
                        LearningSignal.created_at >= cutoff_date
                    )
                )
                .group_by(LearningSignal.signal_type)
            )
            signals = signals.all()

            # Get successful patterns
            successful_patterns = await db.execute(
                select(func.count(SuccessfulQueryPattern.id))
                .where(SuccessfulQueryPattern.team_id == team_id)
            )
            successful_count = successful_patterns.scalar()

            # Get failed patterns
            failed_patterns = await db.execute(
                select(func.count(FailedQueryPattern.id))
                .where(FailedQueryPattern.team_id == team_id)
            )
            failed_count = failed_patterns.scalar()

            # Get rewrite rules
            rewrite_rules = await db.execute(
                select(func.count(QueryRewriteRule.id))
                .where(
                    and_(
                        QueryRewriteRule.team_id == team_id,
                        QueryRewriteRule.is_active == True
                    )
                )
            )
            rules_count = rewrite_rules.scalar()

            # Calculate satisfaction rate
            total_feedback = sum(s.count for s in signals if s.signal_type in [self.SIGNAL_THUMBS_UP, self.SIGNAL_THUMBS_DOWN])
            positive_feedback = next((s.count for s in signals if s.signal_type == self.SIGNAL_THUMBS_UP), 0)
            satisfaction_rate = (positive_feedback / total_feedback * 100) if total_feedback > 0 else 0

            return {
                "team_id": team_id,
                "period_days": days,
                "signals": {s.signal_type: s.count for s in signals},
                "satisfaction_rate": round(satisfaction_rate, 2),
                "total_feedback": total_feedback,
                "learning_stats": {
                    "successful_patterns": successful_count,
                    "failed_patterns": failed_count,
                    "active_rewrite_rules": rules_count
                },
                "generated_at": datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error("analyze_performance_error", error=str(e), team_id=team_id)
            return {}

    # =====================
    # INTERNAL METHODS
    # =====================

    def _get_primary_retrieval_method(self, query_log: QueryLog) -> Optional[str]:
        """Extract primary retrieval method from query log"""
        if not query_log.retrieval_sources:
            return None

        # Count source types
        sources = query_log.retrieval_sources
        if isinstance(sources, list) and sources:
            # Return most common source
            return max(set(sources), key=sources.count)

        return None

    def _calculate_click_strength(self, position: int) -> float:
        """
        Calculate signal strength based on click position

        Position 1 (top result) = higher confidence
        Position 10 = lower confidence
        """
        # Use inverse position with decay
        return min(1.0, 1.0 / position)

    async def _process_feedback_signal(
        self,
        signal: LearningSignal,
        query_log: QueryLog,
        db: AsyncSession
    ):
        """Process feedback signal to extract patterns"""
        try:
            if signal.signal_type == self.SIGNAL_THUMBS_UP:
                # Learn from successful query
                await self._learn_successful_pattern(signal, query_log, db)

            elif signal.signal_type == self.SIGNAL_THUMBS_DOWN:
                # Learn from failed query
                await self._learn_failed_pattern(signal, query_log, db)

        except Exception as e:
            logger.error("process_feedback_signal_error", error=str(e))

    async def _learn_successful_pattern(
        self,
        signal: LearningSignal,
        query_log: QueryLog,
        db: AsyncSession
    ):
        """Extract pattern from successful query"""
        try:
            # Determine pattern type
            pattern_type = self._determine_pattern_type(query_log)

            # Check if pattern already exists
            existing_pattern = await db.execute(
                select(SuccessfulQueryPattern)
                .where(
                    and_(
                        SuccessfulQueryPattern.team_id == signal.team_id,
                        SuccessfulQueryPattern.pattern_type == pattern_type
                    )
                )
            )
            existing_pattern = existing_pattern.scalar_one_or_none()

            if existing_pattern:
                # Update existing pattern
                existing_pattern.success_count += 1
                existing_pattern.total_uses += 1
                existing_pattern.success_rate = existing_pattern.success_count / existing_pattern.total_uses

                # Add example
                examples = existing_pattern.query_examples or []
                if query_log.query_text not in examples:
                    examples.append(query_log.query_text)
                    existing_pattern.query_examples = examples[:10]  # Keep max 10 examples

                existing_pattern.updated_at = datetime.utcnow()

            else:
                # Create new pattern
                pattern = SuccessfulQueryPattern(
                    pattern_id=str(uuid.uuid4()),
                    team_id=signal.team_id,
                    pattern_type=pattern_type,
                    query_template=self._generalize_query(query_log.query_text),
                    query_examples=[query_log.query_text],
                    success_count=1,
                    total_uses=1,
                    success_rate=1.0,
                    winning_strategy={
                        "retrieval_method": signal.retrieval_method,
                        "avg_score": query_log.avg_result_score,
                        "results_count": query_log.results_count
                    },
                    entities_present=query_log.entities_extracted,
                    intent_type=query_log.intent_type,
                    confidence_score=0.8
                )
                db.add(pattern)

            await db.commit()

            logger.info(
                "successful_pattern_learned",
                pattern_type=pattern_type,
                team_id=signal.team_id
            )

        except Exception as e:
            logger.error("learn_successful_pattern_error", error=str(e))

    async def _learn_failed_pattern(
        self,
        signal: LearningSignal,
        query_log: QueryLog,
        db: AsyncSession
    ):
        """Extract pattern from failed query"""
        try:
            failure_type = self._determine_failure_type(query_log)

            # Check if pattern already exists
            existing_pattern = await db.execute(
                select(FailedQueryPattern)
                .where(
                    and_(
                        FailedQueryPattern.team_id == signal.team_id,
                        FailedQueryPattern.failure_type == failure_type
                    )
                )
            )
            existing_pattern = existing_pattern.scalar_one_or_none()

            # Generate improvement suggestions
            suggestions = self._generate_improvement_suggestions(query_log)

            if existing_pattern:
                existing_pattern.failure_count += 1
                existing_pattern.total_occurrences += 1
                existing_pattern.failure_rate = existing_pattern.failure_count / existing_pattern.total_occurrences
                existing_pattern.updated_at = datetime.utcnow()

                # Merge suggestions
                if suggestions:
                    existing_pattern.improvement_suggestions = suggestions

            else:
                pattern = FailedQueryPattern(
                    pattern_id=str(uuid.uuid4()),
                    team_id=signal.team_id,
                    failure_type=failure_type,
                    query_template=self._generalize_query(query_log.query_text),
                    query_examples=[query_log.query_text],
                    failure_count=1,
                    total_occurrences=1,
                    failure_rate=1.0,
                    failure_reasons={
                        "low_score": query_log.top_result_score < 0.5 if query_log.top_result_score else True,
                        "few_results": query_log.results_count < 3
                    },
                    improvement_suggestions=suggestions,
                    entities_present=query_log.entities_extracted,
                    intent_type=query_log.intent_type
                )
                db.add(pattern)

            await db.commit()

            logger.info(
                "failed_pattern_learned",
                failure_type=failure_type,
                team_id=signal.team_id
            )

        except Exception as e:
            logger.error("learn_failed_pattern_error", error=str(e))

    async def _learn_rewrite_pattern(
        self,
        original_query: QueryLog,
        new_query: QueryLog,
        db: AsyncSession
    ):
        """Learn a rewrite rule from successful reformulation"""
        try:
            # TODO: Implement pattern extraction logic
            # This would analyze the difference between queries and create a rewrite rule

            logger.info(
                "rewrite_pattern_learning_placeholder",
                original=original_query.query_text,
                new=new_query.query_text
            )

        except Exception as e:
            logger.error("learn_rewrite_pattern_error", error=str(e))

    async def _process_no_results_signal(
        self,
        signal: LearningSignal,
        query_analysis: Dict[str, Any],
        db: AsyncSession
    ):
        """Process no-results signal to generate suggestions"""
        try:
            # Create failed pattern entry
            suggestions = [
                {"action": "expand_query", "details": "Try adding more context or synonyms"},
                {"action": "broaden_time_range", "details": "Expand time range if temporal filter applied"},
                {"action": "check_spelling", "details": "Verify spelling of key terms"}
            ]

            pattern = FailedQueryPattern(
                pattern_id=str(uuid.uuid4()),
                team_id=signal.team_id,
                failure_type=self.SIGNAL_NO_RESULTS,
                query_template=signal.query_text,
                query_examples=[signal.query_text],
                failure_count=1,
                total_occurrences=1,
                failure_rate=1.0,
                failure_reasons={"reason": "no_results_found"},
                improvement_suggestions=suggestions,
                intent_type=query_analysis.get("query_type")
            )

            db.add(pattern)
            await db.commit()

        except Exception as e:
            logger.error("process_no_results_signal_error", error=str(e))

    def _determine_pattern_type(self, query_log: QueryLog) -> str:
        """Determine pattern type from query log"""
        intent = query_log.intent_type or "general"
        has_entities = bool(query_log.entities_extracted)

        if has_entities:
            return f"{intent}_with_entities"
        return intent

    def _determine_failure_type(self, query_log: QueryLog) -> str:
        """Determine failure type from query log"""
        if query_log.results_count == 0:
            return self.SIGNAL_NO_RESULTS
        elif query_log.top_result_score and query_log.top_result_score < 0.5:
            return self.SIGNAL_LOW_CONFIDENCE
        else:
            return "low_quality"

    def _generalize_query(self, query: str) -> str:
        """Create a generalized template from specific query"""
        # Simple version: just return the query
        # TODO: Implement NER-based generalization
        return query.lower()

    def _generate_improvement_suggestions(self, query_log: QueryLog) -> List[Dict[str, Any]]:
        """Generate suggestions for improving a failed query"""
        suggestions = []

        if query_log.results_count == 0:
            suggestions.append({
                "action": "expand_query",
                "details": "Add more context or use synonyms"
            })

        if query_log.top_result_score and query_log.top_result_score < 0.5:
            suggestions.append({
                "action": "reformulate",
                "details": "Try rephrasing the question"
            })

        if not query_log.entities_extracted:
            suggestions.append({
                "action": "add_entities",
                "details": "Include specific names, projects, or technologies"
            })

        return suggestions


# Global feedback loop service instance
feedback_loop_service = FeedbackLoopService()
