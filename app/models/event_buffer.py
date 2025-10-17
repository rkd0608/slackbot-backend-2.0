"""Event buffer model for webhook fallback storage"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum as SQLEnum, JSON, Index
from datetime import datetime
import enum
from app.core.database import Base


class EventBufferStatus(str, enum.Enum):
    """Event buffer status enum"""
    PENDING = "pending"
    QUEUED = "queued"
    FAILED = "failed"


class EventBuffer(Base):
    """
    Fallback storage for Slack events that fail to queue immediately.
    Ensures zero data loss when RabbitMQ is temporarily unavailable.
    """
    __tablename__ = "event_buffer"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(255), unique=True, index=True, nullable=True)  # Slack's event_id (may be null for some events)
    team_id = Column(String(255), index=True, nullable=False)
    event_type = Column(String(100), index=True)
    event_data = Column(JSON, nullable=False)  # Full event payload
    status = Column(SQLEnum(EventBufferStatus), default=EventBufferStatus.PENDING, index=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index('idx_status_retry', 'status', 'retry_count'),
        Index('idx_created_status', 'created_at', 'status'),
    )

    def __repr__(self):
        return f"<EventBuffer(id={self.id}, event_type={self.event_type}, status={self.status})>"
