"""Learning signal models for self-improving system"""
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, Float, DateTime, JSON, BigInteger, Index, Boolean
from app.core.database import Base


class LearningSignal(Base):
    """
    Captures learning signals from user interactions

    Learning signals help the system improve over time by tracking:
    - User feedback (thumbs up/down)
    - Click-through rates
    - Query reformulations
    - Time spent on results
    - Negative signals (no results, low scores)
    """

    __tablename__ = "learning_signals"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Signal identification
    signal_id = Column(String(100), unique=True, nullable=False, index=True)
    team_id = Column(String(50), nullable=False, index=True)
    user_id = Column(String(50), nullable=False, index=True)

    # Related query
    query_id = Column(String(100), nullable=True, index=True)  # Link to query_logs
    query_text = Column(Text, nullable=False)

    # Signal type
    signal_type = Column(String(50), nullable=False, index=True)
    # Types: 'thumbs_up', 'thumbs_down', 'click', 'reformulation',
    #        'no_results', 'low_confidence', 'copy', 'share'

    # Signal strength (0.0 to 1.0)
    signal_strength = Column(Float, nullable=False, default=1.0)
    # thumbs_up: 1.0, thumbs_down: -1.0, click: 0.5, etc.

    # Context
    signal_context = Column(JSON, nullable=True)
    # Examples:
    # - For clicks: {"result_position": 3, "result_id": "msg123", "score": 0.85}
    # - For reformulation: {"original_query": "...", "new_query": "...", "results_improved": true}
    # - For feedback: {"rating": 5, "comment": "Very helpful", "alternative_query": "..."}

    # Retrieval information (what worked/didn't work)
    retrieval_method = Column(String(50), nullable=True)  # 'semantic', 'keyword', 'entity', 'graph'
    retrieval_score = Column(Float, nullable=True)
    results_count = Column(Integer, nullable=True)

    # Query characteristics
    query_intent = Column(String(50), nullable=True)
    query_entities = Column(JSON, nullable=True)
    query_embedding = Column(JSON, nullable=True)  # For similarity matching

    # Learning metadata
    learned_from = Column(Boolean, default=False, nullable=False, index=True)  # Has this signal been used for learning?
    learning_iteration = Column(Integer, nullable=True)  # Which learning cycle incorporated this

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index('idx_signal_type_created', 'signal_type', 'created_at'),
        Index('idx_team_signal_type', 'team_id', 'signal_type'),
        Index('idx_team_learned', 'team_id', 'learned_from'),
        Index('idx_query_signal', 'query_id', 'signal_type'),
    )


class SuccessfulQueryPattern(Base):
    """
    Stores patterns from successful queries for learning

    When a query gets positive feedback, we extract patterns:
    - What query structure worked well
    - What entities were present
    - What retrieval method worked
    - What context was helpful
    """

    __tablename__ = "successful_query_patterns"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pattern_id = Column(String(100), unique=True, nullable=False, index=True)
    team_id = Column(String(50), nullable=False, index=True)

    # Pattern description
    pattern_type = Column(String(50), nullable=False, index=True)
    # Types: 'entity_query', 'temporal_query', 'code_query', 'who_query', etc.

    # Pattern details
    query_template = Column(Text, nullable=True)  # Generalized query pattern
    query_examples = Column(JSON, nullable=True)  # List of example queries

    # Success metrics
    success_count = Column(Integer, default=1, nullable=False)
    total_uses = Column(Integer, default=1, nullable=False)
    success_rate = Column(Float, default=1.0, nullable=False)
    avg_user_rating = Column(Float, nullable=True)

    # What makes this pattern successful
    winning_strategy = Column(JSON, nullable=True)
    # Example:
    # {
    #   "retrieval_method": "entity_aware",
    #   "query_rewrite": "expand_entities",
    #   "filters": ["temporal", "channel"],
    #   "context_window": 3
    # }

    # Pattern features
    entities_present = Column(JSON, nullable=True)  # Common entity types
    intent_type = Column(String(50), nullable=True)
    temporal_filter = Column(Boolean, default=False)
    channel_filter = Column(Boolean, default=False)

    # Confidence in this pattern
    confidence_score = Column(Float, nullable=False, default=0.5)

    # Usage tracking
    last_used = Column(DateTime, nullable=True)
    times_applied = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_pattern_type_success', 'pattern_type', 'success_rate'),
        Index('idx_team_pattern', 'team_id', 'pattern_type'),
        Index('idx_confidence', 'confidence_score'),
    )


class FailedQueryPattern(Base):
    """
    Stores patterns from failed queries for learning

    When a query gets negative feedback or no results, we extract patterns:
    - What query structures don't work
    - What rewrites might help
    - What retrieval methods failed
    """

    __tablename__ = "failed_query_patterns"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pattern_id = Column(String(100), unique=True, nullable=False, index=True)
    team_id = Column(String(50), nullable=False, index=True)

    # Pattern description
    failure_type = Column(String(50), nullable=False, index=True)
    # Types: 'no_results', 'low_confidence', 'thumbs_down', 'reformulated'

    # Pattern details
    query_template = Column(Text, nullable=True)
    query_examples = Column(JSON, nullable=True)

    # Failure metrics
    failure_count = Column(Integer, default=1, nullable=False)
    total_occurrences = Column(Integer, default=1, nullable=False)
    failure_rate = Column(Float, default=1.0, nullable=False)

    # Why this pattern fails
    failure_reasons = Column(JSON, nullable=True)
    # Example:
    # {
    #   "reason": "entity_not_found",
    #   "missing_entities": ["kubernetes", "deployment"],
    #   "retrieval_method_used": "keyword_only",
    #   "suggested_fix": "use_entity_aware_search"
    # }

    # Suggested improvements
    improvement_suggestions = Column(JSON, nullable=True)
    # Example:
    # [
    #   {"action": "expand_query", "details": "add synonyms"},
    #   {"action": "switch_retrieval", "from": "keyword", "to": "semantic"},
    #   {"action": "broaden_time_range", "details": "expand from 7d to 30d"}
    # ]

    # Pattern features
    entities_present = Column(JSON, nullable=True)
    intent_type = Column(String(50), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_failure_type_rate', 'failure_type', 'failure_rate'),
        Index('idx_team_failure', 'team_id', 'failure_type'),
    )


class QueryRewriteRule(Base):
    """
    Learned query rewrite rules based on feedback

    Stores successful query rewrites that improved results.
    Used by QueryRewriteLearner to automatically improve queries.
    """

    __tablename__ = "query_rewrite_rules"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_id = Column(String(100), unique=True, nullable=False, index=True)
    team_id = Column(String(50), nullable=False, index=True)

    # Rule definition
    rule_type = Column(String(50), nullable=False, index=True)
    # Types: 'entity_expansion', 'synonym_replacement', 'temporal_adjustment',
    #        'context_addition', 'specificity_increase'

    # Pattern matching
    trigger_pattern = Column(Text, nullable=False)  # Regex or keyword pattern
    trigger_intent = Column(String(50), nullable=True)  # Apply only for specific intents

    # Rewrite transformation
    rewrite_template = Column(Text, nullable=False)
    rewrite_examples = Column(JSON, nullable=True)
    # Example:
    # Original: "k8s deployment"
    # Rewritten: "kubernetes deployment OR k8s deployment"

    # Performance metrics
    applications_count = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    improvement_rate = Column(Float, default=0.0, nullable=False)  # % of times rewrite helped
    avg_score_improvement = Column(Float, nullable=True)  # Average increase in result score

    # Rule confidence
    confidence = Column(Float, nullable=False, default=0.5)

    # Rule status
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_applied = Column(DateTime, nullable=True)

    __table_args__ = (
        Index('idx_rule_type_active', 'rule_type', 'is_active'),
        Index('idx_team_active', 'team_id', 'is_active'),
        Index('idx_improvement', 'improvement_rate'),
    )
