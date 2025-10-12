"""User expertise model for tracking people-entity relationships"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, BigInteger, Index, ForeignKey, JSON
from app.core.database import Base


class UserExpertise(Base):
    """Track user expertise in various entities/topics"""

    __tablename__ = "user_expertise"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # User and entity
    user_id = Column(String(100), nullable=False, index=True)
    entity_id = Column(BigInteger, ForeignKey('entities.id'), nullable=False, index=True)

    # Expertise metrics
    mention_count = Column(Integer, default=0)  # Times user mentioned this entity
    message_count = Column(Integer, default=0)  # Messages containing this entity

    # Engagement metrics
    reactions_received = Column(Integer, default=0)  # Reactions on their messages about this entity
    replies_received = Column(Integer, default=0)   # Replies to their messages about this entity
    engagement_score = Column(Float, default=0.0)   # Combined engagement metric

    # Authority metrics
    citation_count = Column(Integer, default=0)      # Times others referenced their answers
    authority_score = Column(Float, default=0.0)     # How authoritative they are on this topic

    # Overall expertise score (0-1)
    expertise_score = Column(Float, default=0.0, index=True)

    # Temporal tracking
    first_interaction = Column(DateTime, nullable=False)
    last_interaction = Column(DateTime, nullable=False)
    recent_activity_score = Column(Float, default=0.0)  # Decay over time

    # Context
    primary_channels = Column(JSON, nullable=True)  # Top channels where they discuss this
    sample_messages = Column(JSON, nullable=True)   # Sample high-quality messages

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_user_entity', 'user_id', 'entity_id', unique=True),
        Index('idx_expertise_score', 'expertise_score'),
        Index('idx_user_scores', 'user_id', 'expertise_score'),
    )


class SignificantEvent(Base):
    """Track significant events in the organization"""

    __tablename__ = "significant_events"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Event details
    event_id = Column(String(100), unique=True, nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)  # deployment, migration, incident, feature_launch
    event_name = Column(String(500), nullable=False)
    event_description = Column(String(2000), nullable=True)

    # Related entities
    entity_ids = Column(JSON, nullable=True)  # List of related entity IDs
    primary_entity_id = Column(BigInteger, ForeignKey('entities.id'), nullable=True, index=True)

    # Temporal
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True)
    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Context
    channels_discussed = Column(JSON, nullable=True)  # Channels where event was discussed
    message_ids = Column(JSON, nullable=True)         # Messages related to this event
    participants = Column(JSON, nullable=True)        # Users involved in the event

    # Metrics
    message_count = Column(Integer, default=0)        # Total messages about this event
    severity = Column(String(20), nullable=True)      # low, medium, high, critical (for incidents)

    # Impact analysis
    before_metrics = Column(JSON, nullable=True)      # Metrics before event
    after_metrics = Column(JSON, nullable=True)       # Metrics after event

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_event_time', 'start_time', 'end_time'),
        Index('idx_event_type_time', 'event_type', 'start_time'),
    )
