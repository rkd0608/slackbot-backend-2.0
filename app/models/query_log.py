"""Query logging and analytics model"""
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, Float, DateTime, JSON, BigInteger, Index
from app.core.database import Base


class QueryLog(Base):
    """Audit trail and analytics for user queries"""

    __tablename__ = "query_logs"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Query identification
    query_id = Column(String(100), unique=True, nullable=False, index=True)
    user_id = Column(String(50), nullable=False, index=True)

    # Query details
    query_text = Column(Text, nullable=False)
    query_embedding = Column(JSON, nullable=True)  # For similarity matching

    # Intent classification
    intent_type = Column(String(100), nullable=True)  # factual, code, summary, etc.
    entities_extracted = Column(JSON, nullable=True)

    # Temporal filters
    date_range_start = Column(DateTime, nullable=True)
    date_range_end = Column(DateTime, nullable=True)

    # Channel filters
    channel_filters = Column(JSON, nullable=True)

    # Retrieval results
    candidates_count = Column(Integer, default=0)
    results_count = Column(Integer, default=0)
    result_message_ids = Column(JSON, nullable=True)  # List of returned message IDs

    # Enhanced retrieval metadata
    retrieval_scores = Column(JSON, nullable=True)  # Scores for each result
    retrieval_sources = Column(JSON, nullable=True)  # Source types (vector/keyword/entity)
    top_result_score = Column(Float, nullable=True)  # Highest relevance score
    avg_result_score = Column(Float, nullable=True)  # Average score across top results

    # Query enhancement tracking
    original_query = Column(Text, nullable=True)  # Query before rewriting
    rewritten_query = Column(Text, nullable=True)  # Query after rewriting
    expanded_queries = Column(JSON, nullable=True)  # Query expansion variants
    query_was_rewritten = Column(Integer, default=0)  # Boolean flag
    query_was_expanded = Column(Integer, default=0)  # Boolean flag

    # Performance metrics
    retrieval_latency_ms = Column(Integer, nullable=True)
    reranking_latency_ms = Column(Integer, nullable=True)
    llm_latency_ms = Column(Integer, nullable=True)
    total_latency_ms = Column(Integer, nullable=True)

    # Cache information
    cache_hit = Column(Integer, default=0)
    cache_key = Column(String(255), nullable=True)

    # User feedback
    user_rating = Column(Integer, nullable=True)  # 1-5 or thumbs up/down
    user_clicked_results = Column(JSON, nullable=True)  # Which results were clicked
    user_copied_content = Column(Integer, default=0)

    # Response
    response_text = Column(Text, nullable=True)
    citations = Column(JSON, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index('idx_user_created', 'user_id', 'created_at'),
        Index('idx_intent_created', 'intent_type', 'created_at'),
    )
