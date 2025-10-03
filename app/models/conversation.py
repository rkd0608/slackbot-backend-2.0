"""Conversation history model"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, JSON, BigInteger, Text, Index
from app.core.database import Base


class Conversation(Base):
    """Stores conversation history for multi-turn interactions"""

    __tablename__ = "conversations"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Conversation identification
    conversation_id = Column(String(100), unique=True, nullable=False, index=True)
    user_id = Column(String(50), nullable=False, index=True)

    # Conversation metadata
    title = Column(String(500), nullable=True)  # Auto-generated from first query
    turn_count = Column(Integer, default=0)

    # Conversation history (list of turns)
    # Each turn: {"role": "user|assistant", "content": "...", "timestamp": "..."}
    history = Column(JSON, nullable=False, default=list)

    # Last query for context
    last_query = Column(Text, nullable=True)
    last_response = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_interaction_at = Column(DateTime, default=datetime.utcnow)

    # Indexes
    __table_args__ = (
        Index('idx_user_updated', 'user_id', 'updated_at'),
        Index('idx_user_last_interaction', 'user_id', 'last_interaction_at'),
    )
