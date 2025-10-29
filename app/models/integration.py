"""External integration models for GitHub, Confluence, Jira, etc."""
from sqlalchemy import Column, String, Integer, DateTime, Boolean, JSON, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.database import Base
import uuid


class Integration(Base):
    """
    Workspace-level integration configuration
    Tracks which external tools are enabled for a workspace
    """
    __tablename__ = "integrations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = Column(String(50), ForeignKey("workspaces.team_id"), nullable=False)

    # Integration type
    integration_type = Column(String(50), nullable=False)  # "github", "confluence", "jira", "notion"

    # Status
    is_active = Column(Boolean, default=True)
    enabled_at = Column(DateTime, default=datetime.utcnow)
    disabled_at = Column(DateTime, nullable=True)

    # Workspace-level configuration
    config = Column(JSON)  # {"default_repos": ["owner/repo1"], "auto_index": true}

    # Admin who enabled this integration
    enabled_by_user_id = Column(String(50), nullable=False)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user_connections = relationship("UserIntegrationConnection", back_populates="integration", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_team_integration', 'team_id', 'integration_type', unique=True),
        Index('idx_active', 'is_active'),
    )


class UserIntegrationConnection(Base):
    """
    Individual user OAuth connection to external tool
    Each user connects their own account for permission-aware access
    """
    __tablename__ = "user_integration_connections"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(50), ForeignKey("users.user_id"), nullable=False)
    team_id = Column(String(50), ForeignKey("workspaces.team_id"), nullable=False)
    integration_id = Column(String(36), ForeignKey("integrations.id"), nullable=False)

    # Integration details (denormalized for performance)
    integration_type = Column(String(50), nullable=False)

    # OAuth credentials (ENCRYPTED with AES-256-GCM)
    access_token_encrypted = Column(Text, nullable=False)
    refresh_token_encrypted = Column(Text, nullable=True)  # Some providers don't use refresh tokens
    token_expires_at = Column(DateTime, nullable=True)

    # External user identity
    external_user_id = Column(String(255))  # GitHub user ID, Atlassian account ID, etc.
    external_username = Column(String(255))
    external_email = Column(String(255), nullable=True)

    # OAuth scopes granted
    scopes = Column(JSON)  # ["repo", "read:org"] for GitHub

    # Status
    is_active = Column(Boolean, default=True)
    connected_at = Column(DateTime, default=datetime.utcnow)
    disconnected_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)

    # Error tracking
    last_error = Column(Text, nullable=True)
    error_count = Column(Integer, default=0)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    integration = relationship("Integration", back_populates="user_connections")

    __table_args__ = (
        Index('idx_user_integration', 'user_id', 'integration_type'),
        Index('idx_team_user', 'team_id', 'user_id'),
        Index('idx_active', 'is_active'),
        Index('idx_external_user', 'external_user_id'),
    )


class ExternalContent(Base):
    """
    Content from external integrations (GitHub, Confluence, Jira)
    Stores code files, docs, issues, etc. with permission metadata
    """
    __tablename__ = "external_contents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = Column(String(50), ForeignKey("workspaces.team_id"), nullable=False)

    # Source tracking
    source_type = Column(String(50), nullable=False)  # "github", "confluence", "jira"
    source_id = Column(String(500), nullable=False)  # Unique ID per source (e.g., "github:owner/repo:path/file.py")
    source_url = Column(String(1000))  # Direct link to original

    # Content
    title = Column(String(1000))
    content = Column(Text)  # Full text content
    content_type = Column(String(50))  # "code", "issue", "pr", "page", "ticket", "readme"
    language = Column(String(50), nullable=True)  # For code: "python", "javascript"

    # Author metadata
    author = Column(String(255))
    author_external_id = Column(String(255))
    created_at_source = Column(DateTime)
    updated_at_source = Column(DateTime)

    # Rich metadata (source-specific, stored as JSON)
    source_metadata = Column(JSON)

    # CRITICAL: Permission tracking
    visibility = Column(String(50), nullable=False)  # "public", "private", "restricted"
    accessible_by_user_ids = Column(JSON)  # List of external user IDs who can access
    accessible_by_team_ids = Column(JSON)  # List of team/org IDs

    # GitHub-specific permission fields
    repository_id = Column(String(255), nullable=True)
    repository_full_name = Column(String(500), nullable=True)
    repository_visibility = Column(String(50), nullable=True)  # "public", "private", "internal"

    # Confluence-specific permission fields
    space_id = Column(String(255), nullable=True)
    space_permissions = Column(JSON)  # {"view": ["user1", "@group1"]}

    # Jira-specific permission fields
    project_id = Column(String(255), nullable=True)
    project_permissions = Column(JSON)

    # Embedding tracking (dual vectors for code)
    vector_id = Column(String(50), unique=True)  # Primary vector ID (code structure for code files)
    vector_id_semantic = Column(String(50), unique=True, nullable=True)  # Semantic context vector

    # Indexing metadata
    indexed_by_user_id = Column(String(50))  # Which user's OAuth token was used
    last_indexed_at = Column(DateTime, default=datetime.utcnow)
    is_deleted = Column(Boolean, default=False)  # Soft delete

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_team_source', 'team_id', 'source_type'),
        Index('idx_source_id', 'source_type', 'source_id', unique=True),
        Index('idx_vector_id', 'vector_id'),
        Index('idx_vector_id_semantic', 'vector_id_semantic'),
        Index('idx_repository', 'repository_id'),
        Index('idx_space', 'space_id'),
        Index('idx_project', 'project_id'),
        Index('idx_content_type', 'content_type'),
        Index('idx_language', 'language'),
        Index('idx_visibility', 'visibility'),
    )


class IntegrationSyncJob(Base):
    """
    Background sync job tracking for integration indexing
    """
    __tablename__ = "integration_sync_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = Column(String(50), ForeignKey("workspaces.team_id"), nullable=False)
    user_connection_id = Column(String(36), ForeignKey("user_integration_connections.id"), nullable=False)

    # Job details
    integration_type = Column(String(50), nullable=False)
    sync_type = Column(String(50), nullable=False)  # "initial", "incremental", "full_resync"

    # What's being synced
    target_resource = Column(String(500))  # e.g., "repo:owner/name", "space:ENG", "project:PROJ"

    # Status
    status = Column(String(50), default="pending")  # "pending", "running", "completed", "failed"
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Progress tracking
    total_items = Column(Integer, default=0)
    processed_items = Column(Integer, default=0)
    failed_items = Column(Integer, default=0)

    # Results
    items_created = Column(Integer, default=0)
    items_updated = Column(Integer, default=0)
    items_deleted = Column(Integer, default=0)

    # Error tracking
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_team_status', 'team_id', 'status'),
        Index('idx_user_connection', 'user_connection_id'),
        Index('idx_status', 'status'),
        Index('idx_integration_type', 'integration_type'),
    )
