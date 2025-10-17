"""Processed events model for idempotency tracking"""
from sqlalchemy import Column, String, DateTime, Index
from datetime import datetime
from app.core.database import Base


class ProcessedEvent(Base):
    """
    Tracks processed Slack events for idempotency.
    Prevents duplicate processing of the same event.
    """
    __tablename__ = "processed_events"

    event_id = Column(String(255), primary_key=True)  # Slack's event_id
    team_id = Column(String(255), index=True, nullable=False)
    event_type = Column(String(100))
    processed_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        # Index for efficient cleanup of old entries
        Index('idx_processed_at_cleanup', 'processed_at'),
        # Composite index for team-specific queries
        Index('idx_team_processed', 'team_id', 'processed_at'),
    )

    def __repr__(self):
        return f"<ProcessedEvent(event_id={self.event_id}, team_id={self.team_id})>"
