"""
Cross-source knowledge graph edge model

Stores relationships between nodes across all sources.
"""
from sqlalchemy import Column, String, Float, DateTime, Index, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
import enum

from app.core.database import Base


class EdgeType(str, enum.Enum):
    """Relationship types between nodes"""
    # Generic
    REFERENCES = "references"
    DISCUSSES = "discusses"
    RELATES_TO = "relates_to"

    # Causal
    CAUSED_BY = "caused_by"
    FIXES = "fixes"
    RESOLVED_BY = "resolved_by"

    # Hierarchical
    PART_OF = "part_of"
    CHILD_OF = "child_of"
    IMPLEMENTS = "implements"

    # Temporal
    FOLLOWS = "follows"
    PRECEDES = "precedes"

    # Code-specific
    MODIFIES = "modifies"
    INTRODUCES = "introduces"
    REMOVES = "removes"
    TESTS = "tests"

    # Collaboration
    MENTIONS = "mentions"
    ASSIGNED_TO = "assigned_to"
    REVIEWED_BY = "reviewed_by"


class DetectionMethod(str, enum.Enum):
    """How the relationship was detected"""
    EXPLICIT_LINK = "explicit_link"  # Direct URL/ID reference
    KEYWORD_MATCH = "keyword_match"  # Text pattern matching
    ENTITY_EXTRACTION = "entity_extraction"  # NLP entity detection
    TEMPORAL_PROXIMITY = "temporal_proximity"  # Time-based correlation
    AUTHOR_CORRELATION = "author_correlation"  # Same author connection
    SEMANTIC_SIMILARITY = "semantic_similarity"  # Vector similarity
    MANUAL = "manual"  # User-created relationship


class CrossSourceEdge(Base):
    """
    Directed relationship between two knowledge graph nodes

    Edges can connect:
    - Same source (e.g., GitHub PR -> GitHub Issue)
    - Different sources (e.g., Slack message -> GitHub PR)
    - Same or different teams (for shared resources)

    Key Design:
    - Directed: source_node -> target_node
    - Typed: edge_type describes relationship semantics
    - Weighted: confidence score (0.0-1.0)
    - Traceable: detection_method and evidence stored
    """

    __tablename__ = "cross_source_edges"

    # Primary key
    id = Column(String(100), primary_key=True)  # UUID

    # Relationship
    source_node_id = Column(
        String(500),
        ForeignKey('cross_source_nodes.canonical_id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    target_node_id = Column(
        String(500),
        ForeignKey('cross_source_nodes.canonical_id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    edge_type = Column(SQLEnum(EdgeType), nullable=False, index=True)

    # Context
    team_id = Column(String(100), nullable=False, index=True)

    # Confidence and evidence
    confidence = Column(Float, default=1.0, nullable=False)  # 0.0 to 1.0
    detection_method = Column(SQLEnum(DetectionMethod), nullable=False)
    evidence = Column(String(2000), nullable=True)  # Why this relationship exists
    link_text = Column(String(1000), nullable=True)  # Actual text/URL that created link

    # Additional metadata (avoiding 'metadata' as it's reserved in SQLAlchemy)
    edge_metadata = Column(JSONB, default=dict, nullable=True)
    # Examples:
    # - {"commit_sha": "abc123", "file_path": "src/foo.py"}
    # - {"slack_message_ts": "1234.5678", "channel": "engineering"}
    # - {"jira_key": "PROJ-123"}

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, nullable=True)

    # Validation status
    is_validated = Column(String(10), default="false", nullable=False)  # Human-verified
    validated_by = Column(String(500), nullable=True)
    validated_at = Column(DateTime, nullable=True)

    # Soft delete
    is_deleted = Column(String(10), default="false", nullable=False)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # Composite indexes for traversal
        Index("idx_edge_source", "source_node_id", "edge_type"),
        Index("idx_edge_target", "target_node_id", "edge_type"),
        Index("idx_edge_team_type", "team_id", "edge_type"),
        Index("idx_edge_confidence", "team_id", "confidence"),
        Index("idx_edge_detection", "detection_method"),

        # Bidirectional lookup
        Index("idx_edge_bidirectional", "source_node_id", "target_node_id"),

        # Time-based queries
        Index("idx_edge_created", "team_id", "created_at"),

        # GIN index for edge_metadata
        Index("idx_edge_metadata", "edge_metadata", postgresql_using="gin"),
    )

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'source_node_id': self.source_node_id,
            'target_node_id': self.target_node_id,
            'edge_type': self.edge_type.value if self.edge_type else None,
            'team_id': self.team_id,
            'confidence': self.confidence,
            'detection_method': self.detection_method.value if self.detection_method else None,
            'evidence': self.evidence,
            'link_text': self.link_text,
            'edge_metadata': self.edge_metadata,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'is_validated': self.is_validated,
            'validated_by': self.validated_by,
            'validated_at': self.validated_at.isoformat() if self.validated_at else None,
        }

    def __repr__(self):
        return (
            f"<CrossSourceEdge("
            f"source={self.source_node_id}, "
            f"target={self.target_node_id}, "
            f"type={self.edge_type}, "
            f"confidence={self.confidence}"
            f")>"
        )
