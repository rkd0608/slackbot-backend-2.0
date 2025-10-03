"""Entity model for knowledge graph"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, JSON, BigInteger, Index
from app.core.database import Base


class Entity(Base):
    """Extracted entities from messages"""

    __tablename__ = "entities"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Entity information
    entity_text = Column(String(500), nullable=False)
    entity_type = Column(String(100), nullable=False, index=True)  # technical, business, people, etc.
    canonical_form = Column(String(500), nullable=False, index=True)  # Normalized form

    # Statistics
    occurrence_count = Column(Integer, default=1)
    channel_count = Column(Integer, default=0)
    related_channels = Column(JSON, nullable=True)  # List of channel IDs

    # Relationships
    related_entities = Column(JSON, nullable=True)  # {entity_id: co_occurrence_count}

    # Embedding reference
    vector_id = Column(String(100), nullable=True)

    # Timestamps
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_type_canonical', 'entity_type', 'canonical_form'),
        Index('idx_occurrence', 'occurrence_count'),
    )


class EntityRelationship(Base):
    """Relationships between entities"""

    __tablename__ = "entity_relationships"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Relationship
    entity_id_1 = Column(BigInteger, nullable=False, index=True)
    entity_id_2 = Column(BigInteger, nullable=False, index=True)
    relationship_type = Column(String(100), nullable=False)  # discusses, relates_to, caused_by, etc.

    # Strength metrics
    co_occurrence_count = Column(Integer, default=1)
    confidence_score = Column(Float, default=0.0)

    # Context
    channels = Column(JSON, nullable=True)  # Where this relationship appears

    # Temporal
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_entities', 'entity_id_1', 'entity_id_2'),
    )
