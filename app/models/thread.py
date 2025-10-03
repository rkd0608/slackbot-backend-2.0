"""Thread model for aggregated thread data"""
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, Text, Integer, Float, DateTime, JSON, Index, BigInteger
from sqlalchemy.dialects.mysql import LONGTEXT
from app.core.database import Base


class Thread(Base):
    """Thread-level aggregations and summaries"""

    __tablename__ = "threads"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Thread identifier
    thread_ts = Column(String(50), unique=True, nullable=False, index=True)
    channel_id = Column(String(50), nullable=False, index=True)
    channel_name = Column(String(255), nullable=True)

    # Parent message reference
    parent_message_id = Column(BigInteger, nullable=False)
    parent_user_id = Column(String(50), nullable=False)
    parent_text = Column(LONGTEXT, nullable=True)

    # Thread statistics
    reply_count = Column(Integer, default=0)
    participant_count = Column(Integer, default=0)
    participant_ids = Column(JSON, nullable=True)  # List of user IDs

    # Content analysis
    summary = Column(Text, nullable=True)  # Generated summary
    topic_classification = Column(String(100), nullable=True)
    entities = Column(JSON, nullable=True)  # Extracted entities
    key_decisions = Column(JSON, nullable=True)  # Identified decisions

    # Importance metrics
    importance_score = Column(Float, default=0.0)
    total_reactions = Column(Integer, default=0)

    # Temporal data
    started_at = Column(DateTime, nullable=False, index=True)
    last_reply_at = Column(DateTime, nullable=True)
    is_active = Column(Integer, default=1)  # MySQL doesn't have boolean, use tinyint

    # Summary embedding reference
    summary_vector_id = Column(String(100), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_channel_started', 'channel_id', 'started_at'),
        Index('idx_importance', 'importance_score'),
    )
