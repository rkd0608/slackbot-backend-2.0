"""
Integration Status Model

Tracks indexing status for each data source (Slack, GitHub, etc.)
and cross-source graph building progress.
"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, JSON, BigInteger, Float, Text
from sqlalchemy.sql import func

from app.core.database import Base


class IntegrationStatus(Base):
    """
    Tracks indexing and integration status for each data source

    One row per workspace per source type.
    Enables progressive integration (add sources over time).
    """

    __tablename__ = "integration_status"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Workspace
    team_id = Column(String(100), nullable=False, index=True)

    # Source type: 'slack', 'github', 'jira', 'confluence', etc.
    source_type = Column(String(50), nullable=False, index=True)

    # Connection status
    is_connected = Column(String(10), default="false", nullable=False)
    connected_at = Column(DateTime, nullable=True)
    disconnected_at = Column(DateTime, nullable=True)

    # Indexing status: 'not_started', 'pending', 'in_progress', 'complete', 'failed', 'paused'
    indexing_status = Column(String(50), default="not_started", nullable=False)
    indexing_started_at = Column(DateTime, nullable=True)
    indexing_completed_at = Column(DateTime, nullable=True)
    indexing_failed_at = Column(DateTime, nullable=True)
    indexing_error = Column(Text, nullable=True)

    # Progress tracking
    total_items = Column(BigInteger, default=0, nullable=False)  # Total items to index
    items_indexed = Column(BigInteger, default=0, nullable=False)  # Items completed
    items_failed = Column(BigInteger, default=0, nullable=False)  # Items that failed
    progress_percentage = Column(Float, default=0.0, nullable=False)  # 0.0 to 100.0

    # Checkpoint for resumability
    last_checkpoint = Column(JSON, nullable=True)
    # Structure: {
    #   "last_channel_id": "C123",
    #   "last_message_ts": "1234567890.123456",
    #   "last_repo_id": "R123",
    #   "batch_number": 5
    # }

    # Entity counts (what was extracted)
    entities_extracted = Column(BigInteger, default=0, nullable=False)
    messages_indexed = Column(BigInteger, default=0, nullable=False)
    files_indexed = Column(BigInteger, default=0, nullable=False)
    code_snippets_indexed = Column(BigInteger, default=0, nullable=False)

    # Last sync (for incremental updates)
    last_full_sync = Column(DateTime, nullable=True)
    last_incremental_sync = Column(DateTime, nullable=True)
    next_scheduled_sync = Column(DateTime, nullable=True)

    # Configuration for this integration
    config = Column(JSON, nullable=True)
    # Structure: {
    #   "index_private_channels": true,
    #   "index_archived_channels": false,
    #   "github_repos": ["org/repo1", "org/repo2"],
    #   "sync_frequency_hours": 24
    # }

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Unique constraint: one status row per team per source
    __table_args__ = (
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'},
    )

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'team_id': self.team_id,
            'source_type': self.source_type,
            'is_connected': self.is_connected == "true",
            'connected_at': self.connected_at.isoformat() if self.connected_at else None,
            'indexing_status': self.indexing_status,
            'indexing_started_at': self.indexing_started_at.isoformat() if self.indexing_started_at else None,
            'indexing_completed_at': self.indexing_completed_at.isoformat() if self.indexing_completed_at else None,
            'progress_percentage': self.progress_percentage,
            'total_items': self.total_items,
            'items_indexed': self.items_indexed,
            'items_failed': self.items_failed,
            'entities_extracted': self.entities_extracted,
            'last_full_sync': self.last_full_sync.isoformat() if self.last_full_sync else None,
            'last_incremental_sync': self.last_incremental_sync.isoformat() if self.last_incremental_sync else None,
        }


class CrossSourceGraph(Base):
    """
    Tracks cross-source graph building status

    Building cross-source relationships (Slack ↔ GitHub edges) is separate
    from individual source indexing and requires both sources to be indexed.
    """

    __tablename__ = "cross_source_graph_status"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Workspace
    team_id = Column(String(100), nullable=False, unique=True, index=True)

    # Graph building status: 'not_started', 'pending', 'in_progress', 'complete', 'failed'
    graph_status = Column(String(50), default="not_started", nullable=False)
    graph_building_started_at = Column(DateTime, nullable=True)
    graph_building_completed_at = Column(DateTime, nullable=True)

    # Progress tracking
    total_edges_to_build = Column(BigInteger, default=0, nullable=False)
    edges_built = Column(BigInteger, default=0, nullable=False)
    edges_failed = Column(BigInteger, default=0, nullable=False)

    # Edge type counts
    slack_to_github_edges = Column(BigInteger, default=0, nullable=False)  # Message → Repo/PR/Issue
    github_to_slack_edges = Column(BigInteger, default=0, nullable=False)  # PR → Discussion
    user_identity_links = Column(BigInteger, default=0, nullable=False)    # Slack User ↔ GitHub User

    # Last graph rebuild
    last_full_rebuild = Column(DateTime, nullable=True)
    last_incremental_update = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IndexingJob(Base):
    """
    Individual indexing job tracking (for observability and debugging)

    Each job represents a discrete unit of work (e.g., index 1000 messages)
    Enables parallel processing and checkpoint/resume
    """

    __tablename__ = "indexing_jobs"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Job identification
    job_id = Column(String(100), unique=True, nullable=False, index=True)  # UUID
    team_id = Column(String(100), nullable=False, index=True)
    source_type = Column(String(50), nullable=False)

    # Job type: 'full_index', 'incremental_sync', 'graph_build', 'entity_extraction'
    job_type = Column(String(50), nullable=False)

    # Status: 'queued', 'running', 'completed', 'failed', 'cancelled'
    status = Column(String(50), default="queued", nullable=False, index=True)

    # Priority: 1 (highest) to 10 (lowest)
    priority = Column(Integer, default=5, nullable=False)

    # Progress
    total_items = Column(BigInteger, default=0, nullable=False)
    items_processed = Column(BigInteger, default=0, nullable=False)
    items_failed = Column(BigInteger, default=0, nullable=False)

    # Job data (what to process)
    job_data = Column(JSON, nullable=True)
    # Structure: {
    #   "channel_ids": ["C123", "C456"],
    #   "repo_ids": ["R123"],
    #   "date_range": {"start": "...", "end": "..."},
    #   "batch_size": 100
    # }

    # Results
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    # Timing
    queued_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)

    # Worker info
    worker_id = Column(String(100), nullable=True)  # Which worker processed this
    retry_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=3, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'job_id': self.job_id,
            'team_id': self.team_id,
            'source_type': self.source_type,
            'job_type': self.job_type,
            'status': self.status,
            'priority': self.priority,
            'progress': {
                'total': self.total_items,
                'processed': self.items_processed,
                'failed': self.items_failed,
                'percentage': (self.items_processed / self.total_items * 100) if self.total_items > 0 else 0
            },
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'retry_count': self.retry_count,
        }
