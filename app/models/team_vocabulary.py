"""
Team Vocabulary Model

Stores company-specific terms and their mappings learned from usage.
"""
from sqlalchemy import Column, String, Integer, Float, DateTime, JSON, Index
from datetime import datetime

from app.core.database import Base


class TeamVocabulary(Base):
    """
    Team-specific vocabulary and term mappings

    Automatically learns:
    - Abbreviations: "k8s" -> "Kubernetes"
    - Company terms: "FE" -> "frontend", "BE" -> "backend"
    - Product names: "portal" -> "customer portal"
    - Internal jargon: "ship it" -> "deploy to production"
    """

    __tablename__ = "team_vocabulary"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Team context
    team_id = Column(String(100), nullable=False, index=True)

    # Term mapping
    term = Column(String(200), nullable=False)  # Abbreviation or shorthand
    canonical_form = Column(String(500), nullable=False)  # Full/expanded form
    term_type = Column(String(50), nullable=False, default="abbreviation")
    # Types: "abbreviation", "synonym", "product_name", "jargon"

    # Learning metadata
    occurrence_count = Column(Integer, default=1, nullable=False)
    # How many times we've seen this mapping

    confidence_score = Column(Float, default=0.5, nullable=False)
    # Confidence in this mapping (0.0-1.0)

    context_examples = Column(JSON, nullable=True, default=list)
    # List of example contexts where term was used
    # [{"text": "...", "timestamp": "..."}, ...]

    # Source of learning
    learned_from = Column(String(50), default="usage", nullable=False)
    # "usage", "manual", "common_abbreviations", "entity_cooccurrence"

    # Timestamps
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Status
    is_active = Column(String(10), default="true", nullable=False)
    # Can be deactivated if found to be incorrect

    __table_args__ = (
        # Unique constraint: one mapping per term per team
        Index("idx_team_term_unique", "team_id", "term", unique=True),
        Index("idx_team_confidence", "team_id", "confidence_score"),
        Index("idx_team_type", "team_id", "term_type"),
    )

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'term': self.term,
            'canonical_form': self.canonical_form,
            'term_type': self.term_type,
            'occurrence_count': self.occurrence_count,
            'confidence_score': self.confidence_score,
            'context_examples': self.context_examples[:3] if self.context_examples else [],
            'learned_from': self.learned_from,
            'first_seen': self.first_seen.isoformat() if self.first_seen else None,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
        }

    def __repr__(self):
        return (
            f"<TeamVocabulary(team_id={self.team_id}, "
            f"term={self.term}, canonical={self.canonical_form})>"
        )
