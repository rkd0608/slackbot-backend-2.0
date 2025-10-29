"""
Base adapter interface for external knowledge source integrations

This module provides the abstract base class that all integration adapters must implement.
This ensures a consistent interface for adding new knowledge sources.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

from app.core.logging import get_logger

logger = get_logger(__name__)


class NodeType(str, Enum):
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


class EdgeType(str, Enum):
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


@dataclass
class SourceCapabilities:
    """Capabilities that a source adapter provides"""

    # Data access
    supports_search: bool = False
    supports_incremental_sync: bool = False
    supports_webhooks: bool = False

    # Content types
    supports_full_text: bool = True
    supports_code: bool = False
    supports_files: bool = False

    # Metadata
    supports_permissions: bool = False
    supports_versioning: bool = False
    supports_comments: bool = False

    # Relationships
    can_detect_links: bool = True
    link_patterns: List[str] = None  # Regex patterns for link detection

    def __post_init__(self):
        if self.link_patterns is None:
            self.link_patterns = []


@dataclass
class UnifiedDocument:
    """
    Unified document representation across all sources

    This is the standard format that all adapters must return.
    It will be converted to CrossSourceNode for storage.
    """

    # Identity (required)
    source: str  # "github", "jira", "confluence", etc.
    source_id: str  # Unique ID within source system
    canonical_id: str  # Globally unique: f"{source}:{source_id}"
    node_type: NodeType

    # Content (required)
    title: str
    content: str  # Full text content
    url: str  # URL to view in source system
    created_at: datetime

    # Content (optional)
    summary: Optional[str] = None

    # Metadata (optional)
    author: Optional[str] = None
    author_id: Optional[str] = None
    updated_at: Optional[datetime] = None

    # Context (optional with defaults)
    visibility: str = "team"  # "public", "team", "private"
    project_context: Optional[Dict[str, Any]] = None  # Project/repo/space metadata
    channel_context: Optional[Dict[str, Any]] = None  # Channel/category metadata
    tags: Optional[List[str]] = None  # Labels, tags, categories

    # Relationships (detected by adapter)
    detected_links: Optional[List[Dict[str, Any]]] = None

    # Embeddings (to be generated)
    vector_id_semantic: Optional[str] = None
    vector_id_code: Optional[str] = None

    # Raw data for debugging
    raw_data: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.detected_links is None:
            self.detected_links = []

        # Ensure canonical_id format
        if not self.canonical_id.startswith(f"{self.source}:"):
            self.canonical_id = f"{self.source}:{self.source_id}"


class BaseSourceAdapter(ABC):
    """
    Abstract base class for all external source integrations

    To add a new integration:
    1. Create a new adapter class inheriting from BaseSourceAdapter
    2. Implement all abstract methods
    3. Register the adapter in IntegrationRegistry
    4. Add OAuth/API key configuration
    5. Run sync to populate knowledge graph
    """

    def __init__(self, team_id: str, credentials: Dict[str, Any]):
        """
        Initialize adapter

        Args:
            team_id: Workspace team ID
            credentials: Authentication credentials (API keys, OAuth tokens, etc.)
        """
        self.team_id = team_id
        self.credentials = credentials
        self.source_name = self.get_source_name()
        self.capabilities = self.get_capabilities()

        logger.info(
            "adapter_initialized",
            source=self.source_name,
            team_id=team_id,
            capabilities=self.capabilities
        )

    @abstractmethod
    def get_source_name(self) -> str:
        """
        Return lowercase source identifier

        Returns:
            Source name (e.g., "github", "jira", "confluence")
        """
        pass

    @abstractmethod
    def get_capabilities(self) -> SourceCapabilities:
        """
        Return capabilities of this source

        Returns:
            SourceCapabilities describing what this adapter can do
        """
        pass

    @abstractmethod
    async def authenticate(self) -> bool:
        """
        Verify authentication credentials

        Returns:
            True if authentication successful, False otherwise
        """
        pass

    @abstractmethod
    async def fetch_incremental(
        self,
        since: datetime,
        limit: Optional[int] = None
    ) -> List[UnifiedDocument]:
        """
        Fetch documents created/updated since timestamp

        This is the primary method for syncing data from the source.
        Should return documents in reverse chronological order (newest first).

        Args:
            since: Only fetch documents updated after this time
            limit: Maximum number of documents to fetch (None = no limit)

        Returns:
            List of UnifiedDocument objects
        """
        pass

    @abstractmethod
    async def fetch_by_id(self, source_id: str) -> Optional[UnifiedDocument]:
        """
        Fetch a specific document by its ID in the source system

        Args:
            source_id: ID in the source system

        Returns:
            UnifiedDocument or None if not found
        """
        pass

    @abstractmethod
    async def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20
    ) -> List[UnifiedDocument]:
        """
        Search within this source

        Args:
            query: Search query string
            filters: Source-specific filters (project, labels, date range, etc.)
            limit: Maximum results

        Returns:
            List of matching UnifiedDocument objects
        """
        pass

    @abstractmethod
    async def detect_outbound_links(
        self,
        document: UnifiedDocument
    ) -> List[Dict[str, Any]]:
        """
        Detect references to other sources in this document

        Should detect:
        - Explicit URLs (e.g., GitHub PR links in Slack)
        - Implicit references (e.g., "JIRA-123", "PR #456")

        Args:
            document: Document to analyze for links

        Returns:
            List of detected links in format:
            [{
                "target_source": "github",
                "target_type": "pull_request",
                "target_id": "456",
                "confidence": 1.0,  # 0.0-1.0
                "evidence": "Full URL found in text",
                "link_text": "https://github.com/org/repo/pull/456"
            }]
        """
        pass

    # Optional methods with default implementations

    async def validate_document(self, document: UnifiedDocument) -> bool:
        """
        Validate document structure and content

        Override this to add source-specific validation

        Args:
            document: Document to validate

        Returns:
            True if valid, False otherwise
        """
        if not document.canonical_id:
            logger.error("document_missing_canonical_id", source=self.source_name)
            return False

        if not document.content and not document.title:
            logger.warning(
                "document_missing_content",
                canonical_id=document.canonical_id
            )
            return False

        return True

    async def enrich_document(self, document: UnifiedDocument) -> UnifiedDocument:
        """
        Enrich document with additional metadata

        Override this to add source-specific enrichment:
        - Generate summaries
        - Extract additional metadata
        - Fetch related documents

        Args:
            document: Document to enrich

        Returns:
            Enriched document
        """
        return document

    def supports_capability(self, capability: str) -> bool:
        """
        Check if adapter supports a specific capability

        Args:
            capability: Capability name (e.g., "search", "webhooks")

        Returns:
            True if supported
        """
        return getattr(self.capabilities, f"supports_{capability}", False)

    async def handle_webhook(self, payload: Dict[str, Any]) -> Optional[UnifiedDocument]:
        """
        Handle webhook event from source

        Override this if source supports webhooks

        Args:
            payload: Webhook payload

        Returns:
            UnifiedDocument if event should be processed, None otherwise
        """
        if not self.capabilities.supports_webhooks:
            raise NotImplementedError(
                f"{self.source_name} does not support webhooks"
            )

        return None

    def get_node_types(self) -> Set[NodeType]:
        """
        Get all node types this adapter can produce

        Override this to specify which node types your adapter supports

        Returns:
            Set of NodeType enums
        """
        return {NodeType.MESSAGE}  # Default: generic message type

    def get_edge_types(self) -> Set[EdgeType]:
        """
        Get all edge types this adapter can detect

        Override this to specify which relationships your adapter can detect

        Returns:
            Set of EdgeType enums
        """
        return {
            EdgeType.REFERENCES,
            EdgeType.RELATES_TO,
            EdgeType.DISCUSSES
        }

    async def get_permissions(
        self,
        document: UnifiedDocument,
        user_id: str
    ) -> bool:
        """
        Check if user has permission to access document

        Override this if source has permission system

        Args:
            document: Document to check
            user_id: User to check permissions for

        Returns:
            True if user can access, False otherwise
        """
        if not self.capabilities.supports_permissions:
            # No permission system - assume public within team
            return True

        return True  # Override in subclass

    def __repr__(self):
        return f"<{self.__class__.__name__}(source={self.source_name}, team={self.team_id})>"
