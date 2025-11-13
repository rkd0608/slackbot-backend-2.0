"""
User Preferences Model

Stores per-user settings for features like daily digest, notifications, etc.
"""
from sqlalchemy import Column, String, Boolean, Integer, DateTime, Index
from datetime import datetime

from app.core.database import Base


class UserPreferences(Base):
    """
    User preferences and settings

    Allows users to control their experience:
    - Daily digest delivery
    - Notification frequency
    - Content filters
    """

    __tablename__ = "user_preferences"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # User identity
    user_id = Column(String(100), nullable=False, index=True)
    team_id = Column(String(100), nullable=False, index=True)

    # Daily Digest Settings
    daily_digest_enabled = Column(Boolean, default=True, nullable=False)
    digest_frequency = Column(String(20), default="daily", nullable=False)
    # Options: "daily", "weekly", "never"

    digest_delivery_time = Column(String(10), default="09:00", nullable=True)
    # Format: "HH:MM" in user's timezone

    digest_timezone = Column(String(50), default="UTC", nullable=True)
    # User's timezone for delivery scheduling

    # Content Preferences
    include_hot_topics = Column(Boolean, default=True, nullable=False)
    include_discussions = Column(Boolean, default=True, nullable=False)
    include_github_links = Column(Boolean, default=True, nullable=False)
    include_questions = Column(Boolean, default=True, nullable=False)
    include_mentions = Column(Boolean, default=True, nullable=False)

    # Filter by channel participation
    only_my_channels = Column(Boolean, default=False, nullable=False)
    # If True, only show content from channels user is in

    # Minimum engagement threshold
    min_topic_mentions = Column(Integer, default=3, nullable=False)
    min_discussion_replies = Column(Integer, default=3, nullable=False)

    # Notification Settings
    proactive_suggestions_enabled = Column(Boolean, default=True, nullable=False)
    # For the thread intelligence feature

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_digest_sent = Column(DateTime, nullable=True)

    __table_args__ = (
        # Unique constraint: one preference per user per team
        Index("idx_user_team_unique", "user_id", "team_id", unique=True),
        Index("idx_digest_enabled", "team_id", "daily_digest_enabled"),
    )

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'user_id': self.user_id,
            'team_id': self.team_id,
            'daily_digest_enabled': self.daily_digest_enabled,
            'digest_frequency': self.digest_frequency,
            'digest_delivery_time': self.digest_delivery_time,
            'digest_timezone': self.digest_timezone,
            'include_hot_topics': self.include_hot_topics,
            'include_discussions': self.include_discussions,
            'include_github_links': self.include_github_links,
            'include_questions': self.include_questions,
            'include_mentions': self.include_mentions,
            'only_my_channels': self.only_my_channels,
            'min_topic_mentions': self.min_topic_mentions,
            'min_discussion_replies': self.min_discussion_replies,
            'proactive_suggestions_enabled': self.proactive_suggestions_enabled,
            'last_digest_sent': self.last_digest_sent.isoformat() if self.last_digest_sent else None,
        }

    def __repr__(self):
        return (
            f"<UserPreferences(user_id={self.user_id}, team_id={self.team_id}, "
            f"digest_enabled={self.daily_digest_enabled})>"
        )
