"""
External knowledge source integrations

This package provides a unified interface for integrating external knowledge sources
(GitHub, Jira, Confluence, etc.) into the knowledge graph.

Usage:
    # Create an adapter
    from app.integrations import IntegrationRegistry

    adapter = IntegrationRegistry.get_adapter(
        source_name="github",
        team_id="T1234567",
        credentials={"access_token": "ghp_..."}
    )

    # Fetch recent updates
    docs = await adapter.fetch_incremental(since=datetime.now() - timedelta(days=7))

    # Search within source
    results = await adapter.search(query="authentication bug", filters={"repo": "backend"})
"""

from app.integrations.base_adapter import (
    BaseSourceAdapter,
    SourceCapabilities,
    UnifiedDocument,
    NodeType,
    EdgeType
)
from app.integrations.registry import IntegrationRegistry

__all__ = [
    "BaseSourceAdapter",
    "SourceCapabilities",
    "UnifiedDocument",
    "NodeType",
    "EdgeType",
    "IntegrationRegistry"
]
