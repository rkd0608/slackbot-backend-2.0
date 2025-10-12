"""Channel model"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, JSON, BigInteger, Text, Float
from app.core.database import Base


class Channel(Base):
    """Channel metadata and statistics"""

    __tablename__ = "channels"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Slack identifiers
    channel_id = Column(String(50), unique=True, nullable=False, index=True)
    channel_name = Column(String(255), nullable=False)
    team_id = Column(String(50), nullable=False)

    # Channel properties
    is_private = Column(Integer, default=0)  # MySQL boolean
    is_archived = Column(Integer, default=0)
    topic = Column(Text, nullable=True)
    purpose = Column(Text, nullable=True)

    # Membership
    member_ids = Column(JSON, nullable=True)  # List of member user IDs
    member_count = Column(Integer, default=0)

    # Activity metrics
    total_messages = Column(BigInteger, default=0)
    messages_per_day = Column(Float, default=0.0)
    active_users = Column(Integer, default=0)
    last_message_at = Column(DateTime, nullable=True)

    # Channel categorization
    category = Column(String(100), nullable=True)  # engineering, product, general, etc.
    tags = Column(JSON, nullable=True)

    # Channel embedding for semantic discovery
    embedding_vector_id = Column(String(100), nullable=True)

    # Indexing controls (for workspace admins)
    is_indexed = Column(Integer, default=1, nullable=False)  # 1=indexed, 0=not indexed
    indexing_enabled = Column(Integer, default=1, nullable=False)  # Admin toggle
    indexing_status = Column(
        String(50),
        default='pending',
        nullable=False
    )  # pending, active, paused, complete
    indexing_started_at = Column(DateTime, nullable=True)
    indexing_completed_at = Column(DateTime, nullable=True)
    last_message_indexed_ts = Column(String(50), nullable=True)  # Slack timestamp

    # Timestamps
    slack_created_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_synced_at = Column(DateTime, nullable=True)
