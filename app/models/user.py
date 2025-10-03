"""User model"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, JSON, BigInteger, Text
from app.core.database import Base


class User(Base):
    """User profiles and activity patterns"""

    __tablename__ = "users"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Slack identifiers
    user_id = Column(String(50), unique=True, nullable=False, index=True)
    team_id = Column(String(50), nullable=False)

    # Profile information
    username = Column(String(255), nullable=True)
    real_name = Column(String(255), nullable=True)
    display_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)

    # User properties
    is_bot = Column(Integer, default=0)
    is_admin = Column(Integer, default=0)
    is_owner = Column(Integer, default=0)
    is_deleted = Column(Integer, default=0)

    # Profile details
    title = Column(String(255), nullable=True)
    timezone = Column(String(100), nullable=True)
    avatar_url = Column(String(500), nullable=True)

    # Activity patterns
    total_messages = Column(BigInteger, default=0)
    channels = Column(JSON, nullable=True)  # List of channel IDs
    active_channels_count = Column(Integer, default=0)

    # Expertise inference
    expertise_areas = Column(JSON, nullable=True)  # Inferred from content
    frequently_mentioned_terms = Column(JSON, nullable=True)
    preferred_topics = Column(JSON, nullable=True)

    # OAuth tokens (encrypted in production)
    access_token = Column(String(500), nullable=True)
    refresh_token = Column(String(500), nullable=True)
    token_expires_at = Column(DateTime, nullable=True)

    # Timestamps
    slack_created_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_active_at = Column(DateTime, nullable=True)
