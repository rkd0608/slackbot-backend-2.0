"""Workspace model for multi-tenant support"""
from datetime import datetime, timedelta
from sqlalchemy import Column, String, Integer, DateTime, JSON, BigInteger, Text, Float
from app.core.database import Base


class Workspace(Base):
    """Slack workspace (team) with subscription and settings"""

    __tablename__ = "workspaces"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Slack identifiers
    team_id = Column(String(50), unique=True, nullable=False, index=True)
    team_name = Column(String(255), nullable=False)
    team_domain = Column(String(255), nullable=True)
    team_url = Column(String(500), nullable=True)

    # Bot installation details
    bot_access_token = Column(Text, nullable=False)  # Encrypted at rest
    bot_user_id = Column(String(50), nullable=False)
    bot_scopes = Column(JSON, nullable=True)  # List of granted scopes

    # Installation details
    installer_user_id = Column(String(50), nullable=False)  # Admin who installed
    installer_email = Column(String(255), nullable=True)
    installed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)  # 1=active, 0=inactive

    # Subscription management
    subscription_status = Column(
        String(50),
        default='trial',
        nullable=False
    )  # trial, active, cancelled, expired, suspended
    subscription_tier = Column(
        String(50),
        default='starter',
        nullable=False
    )  # starter, growth, business, enterprise

    # Trial period
    trial_started_at = Column(DateTime, nullable=True)
    trial_ends_at = Column(DateTime, nullable=True)

    # Billing
    stripe_customer_id = Column(String(255), nullable=True, index=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    stripe_payment_method_id = Column(String(255), nullable=True)
    billing_cycle_anchor = Column(Integer, nullable=True)  # Day of month (1-31)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    last_payment_date = Column(DateTime, nullable=True)
    last_payment_amount = Column(Float, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    # Workspace size and limits
    user_count = Column(Integer, default=0, nullable=False)
    active_user_count = Column(Integer, default=0, nullable=False)  # Non-deleted, non-bot
    last_user_count_sync = Column(DateTime, nullable=True)

    # Usage tracking
    monthly_query_limit = Column(Integer, default=100, nullable=False)
    queries_used_this_month = Column(Integer, default=0, nullable=False)
    queries_reset_at = Column(DateTime, nullable=True)
    total_queries_all_time = Column(BigInteger, default=0, nullable=False)

    # Indexing status
    indexing_status = Column(
        String(50),
        default='pending',
        nullable=False
    )  # pending, in_progress, complete, failed
    indexing_started_at = Column(DateTime, nullable=True)
    indexing_completed_at = Column(DateTime, nullable=True)
    total_messages_indexed = Column(BigInteger, default=0, nullable=False)
    total_channels_indexed = Column(Integer, default=0, nullable=False)

    # Workspace settings
    settings = Column(JSON, nullable=True, default=dict)
    # Settings structure:
    # {
    #   "privacy": {
    #     "index_private_channels": true,
    #     "index_archived_channels": false,
    #     "excluded_channel_ids": []
    #   },
    #   "notifications": {
    #     "slack_dm": true,
    #     "email": true,
    #     "weekly_digest": true
    #   },
    #   "features": {
    #     "ai_answers": true,
    #     "code_search": true,
    #     "analytics": true
    #   }
    # }

    # Enterprise features
    sso_enabled = Column(Integer, default=0, nullable=False)
    custom_retention_days = Column(Integer, nullable=True)
    dedicated_support = Column(Integer, default=0, nullable=False)

    # Analytics cache (denormalized for performance)
    analytics_cache = Column(JSON, nullable=True)
    # Cache structure:
    # {
    #   "queries_today": 0,
    #   "queries_this_week": 0,
    #   "queries_this_month": 0,
    #   "top_channels": [],
    #   "top_users": [],
    #   "avg_satisfaction_score": 0.0,
    #   "last_updated": "2024-01-01T00:00:00Z"
    # }

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_activity_at = Column(DateTime, nullable=True)
    deactivated_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Workspace {self.team_name} ({self.team_id})>"

    @property
    def is_trial(self) -> bool:
        """Check if workspace is in trial period"""
        return self.subscription_status == 'trial'

    @property
    def trial_days_remaining(self) -> int:
        """Get remaining trial days"""
        if not self.trial_ends_at:
            return 0
        delta = self.trial_ends_at - datetime.utcnow()
        return max(0, delta.days)

    @property
    def is_subscription_active(self) -> bool:
        """Check if subscription is active (includes trial)"""
        return self.subscription_status in ['trial', 'active']

    @property
    def has_query_limit_available(self) -> bool:
        """Check if workspace has queries available"""
        return self.queries_used_this_month < self.monthly_query_limit

    @property
    def query_limit_percentage(self) -> float:
        """Get percentage of query limit used"""
        if self.monthly_query_limit == 0:
            return 0.0
        return (self.queries_used_this_month / self.monthly_query_limit) * 100

    def increment_query_count(self):
        """Increment query usage counter"""
        self.queries_used_this_month += 1
        self.total_queries_all_time += 1
        self.last_activity_at = datetime.utcnow()

    def reset_monthly_queries(self):
        """Reset monthly query counter"""
        self.queries_used_this_month = 0
        self.queries_reset_at = datetime.utcnow()


class InstallationLog(Base):
    """Log of workspace installations, uninstallations, and changes"""

    __tablename__ = "installation_logs"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Workspace reference
    team_id = Column(String(50), nullable=False, index=True)

    # Event details
    event_type = Column(
        String(50),
        nullable=False,
        index=True
    )  # installed, uninstalled, reinstalled, token_revoked, settings_changed

    event_data = Column(JSON, nullable=True)
    # Event data structure varies by type:
    # {
    #   "installer_user_id": "U123",
    #   "scopes_added": [],
    #   "scopes_removed": [],
    #   "reason": "user_action",
    #   "metadata": {}
    # }

    # User who triggered event
    user_id = Column(String(50), nullable=True)
    user_email = Column(String(255), nullable=True)

    # IP and user agent for security
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(Text, nullable=True)

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def __repr__(self):
        return f"<InstallationLog {self.team_id} - {self.event_type} at {self.created_at}>"
