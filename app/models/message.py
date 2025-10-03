"""Message model and schema"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, String, Text, Integer, Float, Boolean, DateTime, JSON, Index, BigInteger
from sqlalchemy.dialects.mysql import LONGTEXT
from app.core.database import Base


class Message(Base):
    """Stores complete message history from Slack"""

    __tablename__ = "messages"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Slack identifiers
    message_id = Column(String(50), unique=True, nullable=False, index=True)  # Slack message timestamp
    channel_id = Column(String(50), nullable=False, index=True)
    channel_name = Column(String(255), nullable=True)
    user_id = Column(String(50), nullable=False, index=True)
    user_name = Column(String(255), nullable=True)
    team_id = Column(String(50), nullable=False)

    # Thread information
    thread_ts = Column(String(50), nullable=True, index=True)
    is_thread_parent = Column(Boolean, default=False)
    reply_count = Column(Integer, default=0)
    reply_users_count = Column(Integer, default=0)

    # Message content
    text = Column(LONGTEXT, nullable=True)
    text_processed = Column(LONGTEXT, nullable=True)  # Cleaned and expanded
    message_type = Column(String(50), default="message")
    subtype = Column(String(50), nullable=True)

    # Metadata
    timestamp = Column(DateTime, nullable=False, index=True)
    edited_timestamp = Column(DateTime, nullable=True)

    # Rich content flags
    has_code = Column(Boolean, default=False)
    code_languages = Column(JSON, nullable=True)  # List of detected languages
    has_files = Column(Boolean, default=False)
    file_ids = Column(JSON, nullable=True)  # List of file IDs
    has_links = Column(Boolean, default=False)
    links = Column(JSON, nullable=True)  # List of URLs

    # Social signals
    reaction_count = Column(Integer, default=0)
    reactions = Column(JSON, nullable=True)  # List of reactions
    mentioned_users = Column(JSON, nullable=True)  # List of mentioned user IDs

    # Extracted entities
    entities = Column(JSON, nullable=True)  # List of extracted entities
    topic_tags = Column(JSON, nullable=True)  # ML-generated topics

    # Importance scoring
    importance_score = Column(Float, default=0.0)

    # Full message JSON from Slack
    raw_json = Column(JSON, nullable=False)

    # Edit history
    edit_history = Column(JSON, nullable=True)

    # Vector embedding reference
    vector_id = Column(String(100), nullable=True, unique=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Indexes for performance
    __table_args__ = (
        Index('idx_channel_timestamp', 'channel_id', 'timestamp'),
        Index('idx_user_timestamp', 'user_id', 'timestamp'),
        Index('idx_thread_timestamp', 'thread_ts', 'timestamp'),
        Index('idx_importance', 'importance_score'),
    )
