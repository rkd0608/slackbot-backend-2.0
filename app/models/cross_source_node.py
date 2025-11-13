"""
Cross-source knowledge graph node model

Stores unified representation of content from all sources (Slack, GitHub, Jira, etc.)
in a single table with source-specific metadata in JSONB fields.
"""
from sqlalchemy import Column, String, Text, DateTime, Index, JSON, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
import enum

from app.core.database import Base


class NodeType(str, enum.Enum):
    """Unified node types across all sources"""
    # Slack
    MESSAGE = "message"
    THREAD = "thread"
    FILE = "file"
    CODE_SNIPPET = "code_snippet"

    # GitHub
    REPOSITORY = "repository"
    PULL_REQUEST = "pull_request"
    ISSUE = "issue"
    CODE_FILE = "code_file"
    COMMIT = "commit"
    RELEASE = "release"

    # Jira (future)
    JIRA_ISSUE = "jira_issue"
    JIRA_EPIC = "jira_epic"
    JIRA_SPRINT = "jira_sprint"

    # Confluence (future)
    CONFLUENCE_PAGE = "confluence_page"
    CONFLUENCE_SPACE = "confluence_space"

    # Linear (future)
    LINEAR_ISSUE = "linear_issue"
    LINEAR_PROJECT = "linear_project"

    # Google Drive (future)
    GDRIVE_DOC = "gdrive_doc"
    GDRIVE_SHEET = "gdrive_sheet"
    GDRIVE_SLIDE = "gdrive_slide"

    # Entities (derived/extracted knowledge)
    PERSON = "person"              # People mentioned/identified
    TOPIC = "topic"                # Topics/concepts discussed
    PROJECT = "project"            # Projects mentioned
    TECHNOLOGY = "technology"      # Technologies/tools mentioned
    ORGANIZATION = "organization"  # Companies/orgs mentioned
    LOCATION = "location"          # Locations mentioned
    EVENT = "event"                # Events/meetings mentioned


class CrossSourceNode(Base):
    """
    Unified knowledge graph node representing content from any source

    This table stores all content (messages, issues, PRs, docs, etc.) in a unified format.
    Source-specific details are stored in the metadata JSONB field.

    Key Design Decisions:
    - canonical_id format: "{source}:{source_id}" (e.g., "github:org/repo/issues/123")
    - Supports multi-tenancy via team_id
    - Denormalized for query performance (author, title, content in main table)
    - Source-specific metadata in JSONB for flexibility
    """

    __tablename__ = "cross_source_nodes"

    # Primary identity
    canonical_id = Column(String(500), primary_key=True)  # "github:org/repo/pull/456"
    team_id = Column(String(100), nullable=False, index=True)

    # Source information
    source = Column(String(50), nullable=False, index=True)  # "github", "slack", "jira"
    source_id = Column(String(500), nullable=False)  # ID in source system
    node_type = Column(
        SQLEnum(NodeType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )

    # Core content (denormalized for performance)
    title = Column(String(1000), nullable=False)
    content = Column(Text, nullable=True)  # Full text content
    summary = Column(Text, nullable=True)  # AI-generated summary

    # Access
    url = Column(String(1000), nullable=False)  # URL to view in source
    visibility = Column(String(50), default="team")  # "public", "team", "private"

    # Attribution
    author = Column(String(500), nullable=True)
    author_id = Column(String(500), nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=True)
    indexed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Context and metadata
    project_context = Column(JSONB, nullable=True)  # Project/repo/space metadata
    channel_context = Column(JSONB, nullable=True)  # Channel/category metadata
    tags = Column(JSONB, default=list, nullable=True)  # Labels, tags

    # Embeddings (references to Pinecone vectors)
    vector_id_semantic = Column(String(500), nullable=True)  # Semantic embedding vector ID
    vector_id_code = Column(String(500), nullable=True)  # Code-specific embedding vector ID

    # Source-specific metadata (flexible JSONB)
    source_metadata = Column(JSONB, default=dict, nullable=True)
    # Examples:
    # - GitHub PR: {"state": "open", "draft": false, "mergeable": true, "additions": 123}
    # - Jira: {"status": "In Progress", "story_points": 5, "sprint": "Sprint 23"}
    # - Slack: {"reactions": [...], "reply_count": 5, "is_pinned": true}

    # Entity-specific metadata (for PERSON, TOPIC, PROJECT, etc.)
    entity_metadata = Column(JSONB, default=dict, nullable=True)
    # Examples:
    # - PERSON: {
    #     "canonical_form": "john_doe",
    #     "variations": ["John Doe", "J. Doe", "@john"],
    #     "occurrence_count": 145,
    #     "entity_type": "PERSON",
    #     "expertise_areas": ["authentication", "backend"],
    #     "channels": ["C123", "C456"],
    #     "first_seen": "2024-01-15T10:00:00Z",
    #     "last_seen": "2025-01-23T15:30:00Z"
    #   }
    # - TOPIC: {
    #     "canonical_form": "authentication",
    #     "synonyms": ["auth", "login", "oauth"],
    #     "occurrence_count": 230,
    #     "related_technologies": ["OAuth", "JWT", "SAML"],
    #     "trending_score": 0.85
    #   }
    # - PROJECT: {
    #     "canonical_form": "mobile_app_v2",
    #     "status": "active",
    #     "team_size": 5,
    #     "start_date": "2024-Q1"
    #   }

    # Graph analytics (computed periodically)
    importance_score = Column(JSONB, default=dict, nullable=True)
    # {
    #   "pagerank": 0.0145,           # PageRank score (importance in graph)
    #   "centrality": 0.82,           # Betweenness centrality (connector role)
    #   "degree": 45,                 # Number of connections
    #   "in_degree": 30,              # Incoming edges
    #   "out_degree": 15,             # Outgoing edges
    #   "clustering_coefficient": 0.6, # How interconnected neighbors are
    #   "community_id": "eng_team_1", # Detected community
    #   "last_computed": "2025-01-23T12:00:00Z"
    # }

    # Soft delete
    is_deleted = Column(String(10), default="false", nullable=False)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # Composite indexes for common query patterns
        Index("idx_node_team_source", "team_id", "source"),
        Index("idx_node_team_type", "team_id", "node_type"),
        Index("idx_node_team_created", "team_id", "created_at"),
        Index("idx_node_source_id", "source", "source_id"),
        Index("idx_node_author", "team_id", "author"),
        Index("idx_node_visibility", "team_id", "visibility"),

        # GIN indexes for JSONB fields
        Index("idx_node_project_context", "project_context", postgresql_using="gin"),
        Index("idx_node_tags", "tags", postgresql_using="gin"),
        Index("idx_node_source_metadata", "source_metadata", postgresql_using="gin"),
        Index("idx_node_entity_metadata", "entity_metadata", postgresql_using="gin"),
        Index("idx_node_importance_score", "importance_score", postgresql_using="gin"),
    )

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'canonical_id': self.canonical_id,
            'team_id': self.team_id,
            'source': self.source,
            'source_id': self.source_id,
            'node_type': self.node_type.value if self.node_type else None,
            'title': self.title,
            'content': self.content,
            'summary': self.summary,
            'url': self.url,
            'visibility': self.visibility,
            'author': self.author,
            'author_id': self.author_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'indexed_at': self.indexed_at.isoformat() if self.indexed_at else None,
            'project_context': self.project_context,
            'channel_context': self.channel_context,
            'tags': self.tags,
            'vector_id_semantic': self.vector_id_semantic,
            'vector_id_code': self.vector_id_code,
            'source_metadata': self.source_metadata,
            'entity_metadata': self.entity_metadata,
            'importance_score': self.importance_score,
            'is_deleted': self.is_deleted,
        }

    def __repr__(self):
        return f"<CrossSourceNode(canonical_id={self.canonical_id}, type={self.node_type}, source={self.source})>"
