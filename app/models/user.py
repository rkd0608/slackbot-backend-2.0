"""User and authentication models"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, JSON, BigInteger, Text, ForeignKey
from sqlalchemy.orm import relationship
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

    # Authentication fields
    password_hash = Column(Text, nullable=True)  # For password-based auth
    role = Column(String(50), default='member', nullable=False)  # admin, member, viewer
    last_login_at = Column(DateTime, nullable=True)
    profile_completed = Column(Integer, default=0, nullable=False)  # 0=incomplete, 1=complete (onboarding)

    # Timestamps
    slack_created_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_active_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<User {self.username} ({self.user_id})>"

    @property
    def is_workspace_admin(self) -> bool:
        """Check if user is workspace admin"""
        return bool(self.is_admin) or self.role == 'admin'


class APIKey(Base):
    """API keys for programmatic access to the API"""

    __tablename__ = "api_keys"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Key details
    key_hash = Column(String(255), unique=True, nullable=False, index=True)
    key_prefix = Column(String(20), nullable=False)  # First few chars for identification
    name = Column(String(255), nullable=False)  # User-friendly name
    description = Column(Text, nullable=True)

    # Ownership
    team_id = Column(String(50), nullable=False, index=True)
    created_by_user_id = Column(BigInteger, ForeignKey('users.id'), nullable=True)

    # Status and permissions
    is_active = Column(Integer, default=1, nullable=False)
    scopes = Column(JSON, nullable=True)  # JSON array of allowed scopes

    # Usage tracking
    last_used_at = Column(DateTime, nullable=True)
    usage_count = Column(BigInteger, default=0, nullable=False)

    # Expiration
    expires_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    created_by = relationship("User", backref="api_keys", foreign_keys=[created_by_user_id])

    def __repr__(self):
        return f"<APIKey {self.name} ({self.key_prefix})>"

    @property
    def is_expired(self) -> bool:
        """Check if API key has expired"""
        if not self.expires_at:
            return False
        return self.expires_at < datetime.utcnow()

    @property
    def is_valid(self) -> bool:
        """Check if API key is active and not expired"""
        return bool(self.is_active) and not self.is_expired
