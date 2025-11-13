"""
Query Rewrite Learner

Learns from user feedback to automatically improve queries.

Uses data from:
- Successful query patterns (thumbs up)
- Failed query patterns (thumbs down, no results)
- Query reformulations (user tried again with different wording)
- Query rewrite rules (learned transformations)
"""
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc
import re
import uuid

from app.core.logging import get_logger
from app.models.learning_signal import (
    LearningSignal,
    SuccessfulQueryPattern,
    FailedQueryPattern,
    QueryRewriteRule
)
from app.models.query_log import QueryLog

logger = get_logger(__name__)


class QueryRewriteLearner:
    """
    Learns query rewrite rules from feedback data

    Key capabilities:
    - Applies learned rewrite rules
    - Suggests improvements for failing queries
    - Learns from successful reformulations
    - Tracks rewrite effectiveness
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def apply_learned_rewrites(
        self,
        query: str,
        team_id: str,
        query_intent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Apply learned rewrite rules to improve query

        Args:
            query: Original query
            team_id: Workspace team ID
            query_intent: Optional query intent/type

        Returns:
            Dict with:
            - rewritten_query: str
            - rules_applied: List[Dict]
            - confidence: float
        """
        try:
            # Get active rewrite rules for this team
            rules_query = select(QueryRewriteRule).where(
                and_(
                    QueryRewriteRule.team_id == team_id,
                    QueryRewriteRule.is_active == True,
                    QueryRewriteRule.improvement_rate > 0.3  # Only use successful rules
                )
            ).order_by(desc(QueryRewriteRule.improvement_rate))

            if query_intent:
                rules_query = rules_query.where(
                    or_(
                        QueryRewriteRule.trigger_intent == query_intent,
                        QueryRewriteRule.trigger_intent == None
                    )
                )

            result = await self.db.execute(rules_query.limit(10))
            rules = result.scalars().all()

            if not rules:
                return {
                    'rewritten_query': query,
                    'rules_applied': [],
                    'confidence': 1.0
                }

            # Apply matching rules
            rewritten = query
            rules_applied = []
            total_confidence = 1.0

            for rule in rules:
                # Check if rule pattern matches
                if self._rule_matches(rule, rewritten):
                    # Apply rewrite
                    new_query = self._apply_rule(rule, rewritten)

                    if new_query != rewritten:
                        rules_applied.append({
                            'rule_id': rule.rule_id,
                            'rule_type': rule.rule_type,
                            'original': rewritten,
                            'rewritten': new_query,
                            'confidence': rule.confidence
                        })

                        rewritten = new_query
                        total_confidence *= rule.confidence

                        # Update rule usage stats
                        rule.applications_count += 1
                        rule.last_applied = datetime.utcnow()

            if rules_applied:
                await self.db.commit()

                logger.info(
                    "learned_rewrites_applied",
                    query=query,
                    rewritten=rewritten,
                    rules_count=len(rules_applied),
                    team_id=team_id
                )

            return {
                'rewritten_query': rewritten,
                'rules_applied': rules_applied,
                'confidence': total_confidence
            }

        except Exception as e:
            logger.error("apply_learned_rewrites_error", error=str(e))
            return {
                'rewritten_query': query,
                'rules_applied': [],
                'confidence': 1.0,
                'error': str(e)
            }

    async def get_improvement_suggestions(
        self,
        query: str,
        team_id: str,
        query_log: Optional[QueryLog] = None
    ) -> List[Dict[str, Any]]:
        """
        Get suggestions for improving a query based on learned patterns

        Args:
            query: Current query
            team_id: Workspace team ID
            query_log: Optional query log with results

        Returns:
            List of improvement suggestions
        """
        suggestions = []

        try:
            # Check if similar queries have failed before
            similar_failures = await self._find_similar_failed_patterns(
                query, team_id
            )

            for pattern in similar_failures:
                if pattern.improvement_suggestions:
                    for suggestion in pattern.improvement_suggestions:
                        suggestions.append({
                            'type': 'avoid_failure',
                            'action': suggestion.get('action'),
                            'details': suggestion.get('details'),
                            'confidence': 1.0 - pattern.failure_rate,
                            'based_on': f"{pattern.failure_count} similar failed queries"
                        })

            # Check for successful patterns we could learn from
            similar_successes = await self._find_similar_successful_patterns(
                query, team_id
            )

            for pattern in similar_successes:
                if pattern.winning_strategy:
                    strategy = pattern.winning_strategy
                    suggestions.append({
                        'type': 'apply_success_pattern',
                        'action': 'use_strategy',
                        'details': strategy,
                        'confidence': pattern.success_rate,
                        'based_on': f"{pattern.success_count} successful queries"
                    })

            # If query has low results, suggest expansions
            if query_log and query_log.results_count < 3:
                suggestions.append({
                    'type': 'expand_query',
                    'action': 'add_synonyms',
                    'details': 'Try adding related terms or synonyms',
                    'confidence': 0.7
                })

            return suggestions

        except Exception as e:
            logger.error("get_improvement_suggestions_error", error=str(e))
            return []

    async def learn_from_reformulation(
        self,
        original_query: str,
        successful_query: str,
        team_id: str,
        improvement_metrics: Dict[str, Any]
    ) -> Optional[QueryRewriteRule]:
        """
        Learn a rewrite rule from successful query reformulation

        Args:
            original_query: Query that didn't work well
            successful_query: Reformulated query that worked better
            team_id: Workspace team ID
            improvement_metrics: How much better (score increase, etc.)

        Returns:
            Created QueryRewriteRule or None
        """
        try:
            # Analyze the transformation
            transformation = self._analyze_transformation(
                original_query,
                successful_query
            )

            if not transformation:
                return None

            # Check if similar rule already exists
            existing = await self.db.execute(
                select(QueryRewriteRule).where(
                    and_(
                        QueryRewriteRule.team_id == team_id,
                        QueryRewriteRule.rule_type == transformation['type'],
                        QueryRewriteRule.trigger_pattern == transformation['pattern']
                    )
                )
            )
            existing_rule = existing.scalar_one_or_none()

            if existing_rule:
                # Update existing rule
                existing_rule.applications_count += 1
                existing_rule.success_count += 1
                existing_rule.improvement_rate = (
                    existing_rule.success_count / existing_rule.applications_count
                )
                existing_rule.avg_score_improvement = improvement_metrics.get('score_improvement', 0.0)
                existing_rule.updated_at = datetime.utcnow()

                await self.db.commit()

                logger.info(
                    "rewrite_rule_updated",
                    rule_id=existing_rule.rule_id,
                    improvement_rate=existing_rule.improvement_rate
                )

                return existing_rule

            # Create new rule
            rule = QueryRewriteRule(
                rule_id=str(uuid.uuid4()),
                team_id=team_id,
                rule_type=transformation['type'],
                trigger_pattern=transformation['pattern'],
                rewrite_template=transformation['template'],
                rewrite_examples=[{
                    'original': original_query,
                    'rewritten': successful_query,
                    'improvement': improvement_metrics
                }],
                applications_count=1,
                success_count=1,
                improvement_rate=1.0,
                avg_score_improvement=improvement_metrics.get('score_improvement', 0.0),
                confidence=0.7,  # Start with moderate confidence
                is_active=True
            )

            self.db.add(rule)
            await self.db.commit()

            logger.info(
                "rewrite_rule_learned",
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                team_id=team_id
            )

            return rule

        except Exception as e:
            logger.error("learn_from_reformulation_error", error=str(e))
            await self.db.rollback()
            return None

    # ===================
    # HELPER METHODS
    # ===================

    def _rule_matches(self, rule: QueryRewriteRule, query: str) -> bool:
        """Check if rule pattern matches query"""
        try:
            pattern = rule.trigger_pattern

            # Simple keyword matching
            if not pattern.startswith('^') and not pattern.endswith('$'):
                # Treat as keywords
                keywords = pattern.lower().split()
                query_lower = query.lower()
                return any(kw in query_lower for kw in keywords)

            # Regex matching
            return bool(re.search(pattern, query, re.IGNORECASE))

        except Exception:
            return False

    def _apply_rule(self, rule: QueryRewriteRule, query: str) -> str:
        """Apply rewrite rule to query"""
        try:
            template = rule.rewrite_template
            rule_type = rule.rule_type

            if rule_type == 'entity_expansion':
                # Expand abbreviations
                return self._expand_entities(query, template)

            elif rule_type == 'synonym_replacement':
                # Add synonyms
                return self._add_synonyms(query, template)

            elif rule_type == 'context_addition':
                # Add missing context
                return f"{query} {template}"

            elif rule_type == 'specificity_increase':
                # Make more specific
                return template.format(query=query)

            else:
                # Generic template application
                return template.format(query=query)

        except Exception as e:
            logger.error("apply_rule_error", error=str(e), rule_id=rule.rule_id)
            return query

    def _expand_entities(self, query: str, template: str) -> str:
        """Expand abbreviations in query"""
        # Parse template as JSON mapping
        try:
            import json
            expansions = json.loads(template)

            expanded = query
            for abbr, full in expansions.items():
                # Replace whole words only
                pattern = r'\b' + re.escape(abbr) + r'\b'
                expanded = re.sub(pattern, f"{abbr} {full}", expanded, flags=re.IGNORECASE)

            return expanded

        except Exception:
            return query

    def _add_synonyms(self, query: str, template: str) -> str:
        """Add synonyms to query"""
        try:
            import json
            synonyms = json.loads(template)

            additions = []
            for term, syns in synonyms.items():
                if term.lower() in query.lower():
                    additions.extend(syns)

            if additions:
                return f"{query} {' '.join(additions)}"

            return query

        except Exception:
            return query

    def _analyze_transformation(
        self,
        original: str,
        successful: str
    ) -> Optional[Dict[str, str]]:
        """
        Analyze what transformation was applied

        Returns:
            Dict with type, pattern, template or None
        """
        orig_lower = original.lower()
        succ_lower = successful.lower()

        # Check if expansion (added words)
        orig_words = set(orig_lower.split())
        succ_words = set(succ_lower.split())
        added_words = succ_words - orig_words

        if added_words:
            # Context addition or expansion
            return {
                'type': 'context_addition',
                'pattern': original.lower(),
                'template': ' '.join(added_words)
            }

        # Check if synonym replacement
        if len(orig_words) == len(succ_words):
            return {
                'type': 'synonym_replacement',
                'pattern': original.lower(),
                'template': successful
            }

        # Check if specificity increase
        if successful in original or original in successful:
            return {
                'type': 'specificity_increase',
                'pattern': original.lower(),
                'template': successful
            }

        # Generic rewrite
        return {
            'type': 'generic_rewrite',
            'pattern': original.lower(),
            'template': successful
        }

    async def _find_similar_failed_patterns(
        self,
        query: str,
        team_id: str,
        limit: int = 5
    ) -> List[FailedQueryPattern]:
        """Find failed query patterns similar to this query"""
        # Simple keyword-based matching
        keywords = set(re.findall(r'\b\w{4,}\b', query.lower()))

        result = await self.db.execute(
            select(FailedQueryPattern).where(
                and_(
                    FailedQueryPattern.team_id == team_id,
                    FailedQueryPattern.failure_rate > 0.5
                )
            ).order_by(desc(FailedQueryPattern.failure_count)).limit(limit * 2)
        )
        all_patterns = result.scalars().all()

        # Score by keyword overlap
        scored_patterns = []
        for pattern in all_patterns:
            template_keywords = set(re.findall(r'\b\w{4,}\b', pattern.query_template.lower()))
            overlap = len(keywords & template_keywords)

            if overlap >= 2:  # At least 2 keywords match
                scored_patterns.append((pattern, overlap))

        # Sort by overlap and return top matches
        scored_patterns.sort(key=lambda x: x[1], reverse=True)
        return [p[0] for p in scored_patterns[:limit]]

    async def _find_similar_successful_patterns(
        self,
        query: str,
        team_id: str,
        limit: int = 5
    ) -> List[SuccessfulQueryPattern]:
        """Find successful query patterns similar to this query"""
        keywords = set(re.findall(r'\b\w{4,}\b', query.lower()))

        result = await self.db.execute(
            select(SuccessfulQueryPattern).where(
                and_(
                    SuccessfulQueryPattern.team_id == team_id,
                    SuccessfulQueryPattern.success_rate > 0.7
                )
            ).order_by(desc(SuccessfulQueryPattern.success_count)).limit(limit * 2)
        )
        all_patterns = result.scalars().all()

        # Score by keyword overlap
        scored_patterns = []
        for pattern in all_patterns:
            template_keywords = set(re.findall(r'\b\w{4,}\b', pattern.query_template.lower()))
            overlap = len(keywords & template_keywords)

            if overlap >= 2:
                scored_patterns.append((pattern, overlap))

        scored_patterns.sort(key=lambda x: x[1], reverse=True)
        return [p[0] for p in scored_patterns[:limit]]


def get_rewrite_learner(db: AsyncSession) -> QueryRewriteLearner:
    """Get rewrite learner instance"""
    return QueryRewriteLearner(db)
