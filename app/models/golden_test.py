"""Golden test set model for evaluation"""
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, Float, DateTime, JSON, BigInteger, Index
from app.core.database import Base


class GoldenTest(Base):
    """Golden test cases for evaluating retrieval and answer quality"""

    __tablename__ = "golden_tests"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Test identification
    test_id = Column(String(100), unique=True, nullable=False, index=True)
    test_name = Column(String(255), nullable=False)

    # Test query
    query = Column(Text, nullable=False)
    query_type = Column(String(100), nullable=True)  # factual, code, summary, etc.

    # Expected results
    expected_message_ids = Column(JSON, nullable=True)  # List of message IDs that should be retrieved
    expected_answer = Column(Text, nullable=True)  # Reference answer (optional)
    expected_entities = Column(JSON, nullable=True)  # Expected entities to extract
    expected_channels = Column(JSON, nullable=True)  # Expected channel filters

    # Evaluation criteria
    min_relevance_score = Column(Float, default=0.7)  # Minimum acceptable relevance
    required_in_top_k = Column(Integer, default=5)  # Expected messages must appear in top K

    # Metadata
    category = Column(String(100), nullable=True)  # e.g., "engineering", "product", "support"
    difficulty = Column(String(50), nullable=True)  # easy, medium, hard
    tags = Column(JSON, nullable=True)  # Additional tags for filtering

    # Test status
    is_active = Column(Integer, default=1)  # Active in evaluation runs

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_category_active', 'category', 'is_active'),
        Index('idx_query_type_active', 'query_type', 'is_active'),
    )


class EvaluationRun(Base):
    """Track evaluation runs against golden test set"""

    __tablename__ = "evaluation_runs"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Run identification
    run_id = Column(String(100), unique=True, nullable=False, index=True)
    run_name = Column(String(255), nullable=True)

    # Configuration
    model_version = Column(String(100), nullable=True)  # e.g., "gpt-4o-mini-2024-07-18"
    retrieval_config = Column(JSON, nullable=True)  # Settings used for this run

    # Overall metrics
    total_tests = Column(Integer, default=0)
    passed_tests = Column(Integer, default=0)
    failed_tests = Column(Integer, default=0)

    # Aggregate scores
    avg_precision_at_5 = Column(Float, nullable=True)
    avg_recall_at_5 = Column(Float, nullable=True)
    avg_mrr = Column(Float, nullable=True)  # Mean Reciprocal Rank
    avg_ndcg = Column(Float, nullable=True)  # Normalized Discounted Cumulative Gain

    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index('idx_started_at', 'started_at'),
    )


class EvaluationResult(Base):
    """Individual test results from evaluation runs"""

    __tablename__ = "evaluation_results"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Foreign keys
    run_id = Column(String(100), nullable=False, index=True)
    test_id = Column(String(100), nullable=False, index=True)

    # Test execution
    query = Column(Text, nullable=False)
    retrieved_message_ids = Column(JSON, nullable=True)  # What was actually retrieved
    retrieval_scores = Column(JSON, nullable=True)  # Scores for retrieved results

    # Metrics
    precision_at_5 = Column(Float, nullable=True)
    recall_at_5 = Column(Float, nullable=True)
    reciprocal_rank = Column(Float, nullable=True)
    ndcg_at_5 = Column(Float, nullable=True)

    # Answer quality (if applicable)
    generated_answer = Column(Text, nullable=True)
    answer_similarity_score = Column(Float, nullable=True)  # Similarity to expected answer

    # Pass/fail
    passed = Column(Integer, default=0)  # Boolean
    failure_reason = Column(Text, nullable=True)

    # Performance
    latency_ms = Column(Integer, nullable=True)

    # Timestamps
    evaluated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_run_test', 'run_id', 'test_id'),
        Index('idx_run_passed', 'run_id', 'passed'),
    )
