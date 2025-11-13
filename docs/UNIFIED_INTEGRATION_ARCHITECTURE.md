# Unified Integration Architecture

## Overview

The unified integration architecture enables the AI system to seamlessly integrate multiple knowledge sources (Slack, GitHub, Jira, Confluence, Linear, Google Drive, etc.) into a single, intelligent knowledge graph.

**Key Innovation**: Any new integration (Jira, Confluence, Linear, etc.) can be added by implementing a single adapter class - no changes to the core system required.

## Architecture Components

### 1. Base Adapter Pattern

**Location**: `app/integrations/base_adapter.py`

All integrations implement the `BaseSourceAdapter` interface:

```python
class MyNewSourceAdapter(BaseSourceAdapter):
    def get_source_name(self) -> str:
        return "jira"  # or "confluence", "linear", etc.

    def get_capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            supports_search=True,
            supports_incremental_sync=True,
            supports_webhooks=True,
            # ... other capabilities
        )

    async def fetch_incremental(self, since: datetime) -> List[UnifiedDocument]:
        # Fetch data updated since timestamp
        pass

    async def search(self, query: str, filters: Dict) -> List[UnifiedDocument]:
        # Search within this source
        pass

    async def detect_outbound_links(self, document: UnifiedDocument) -> List[Dict]:
        # Detect references to other sources
        pass
```

**Standardized Node Types** (`NodeType` enum):
- **Slack**: MESSAGE, THREAD, FILE, CODE_SNIPPET
- **GitHub**: REPOSITORY, PULL_REQUEST, ISSUE, CODE_FILE, COMMIT, RELEASE
- **Jira** (future): JIRA_ISSUE, JIRA_EPIC, JIRA_SPRINT
- **Confluence** (future): CONFLUENCE_PAGE, CONFLUENCE_SPACE
- **Linear** (future): LINEAR_ISSUE, LINEAR_PROJECT
- **Google Drive** (future): GDRIVE_DOC, GDRIVE_SHEET, GDRIVE_SLIDE

**Standardized Edge Types** (`EdgeType` enum):
- **Generic**: REFERENCES, DISCUSSES, RELATES_TO
- **Causal**: CAUSED_BY, FIXES, RESOLVED_BY
- **Hierarchical**: PART_OF, CHILD_OF, IMPLEMENTS
- **Temporal**: FOLLOWS, PRECEDES
- **Code**: MODIFIES, INTRODUCES, REMOVES, TESTS
- **Collaboration**: MENTIONS, ASSIGNED_TO, REVIEWED_BY

### 2. Unified Knowledge Graph

**Database Models**:
- `app/models/cross_source_node.py` - Stores content from ALL sources
- `app/models/cross_source_edge.py` - Stores relationships between nodes

**Key Design Decisions**:

1. **Canonical ID Format**: `{source}:{source_id}`
   - Example: `github:org/repo/pull/456`
   - Example: `jira:PROJ-123`
   - Example: `slack:C123456-1234567890.123456`

2. **Multi-Tenancy**: All queries filtered by `team_id`

3. **Flexible Metadata**: Source-specific details in JSONB fields
   ```sql
   -- GitHub PR metadata:
   source_metadata: {
     "state": "open",
     "draft": false,
     "mergeable": true,
     "additions": 123,
     "deletions": 45
   }

   -- Jira issue metadata:
   source_metadata: {
     "status": "In Progress",
     "story_points": 5,
     "sprint": "Sprint 23",
     "priority": "High"
   }
   ```

4. **Dual Embeddings**: Each node can have both semantic and code-specific embeddings
   - `vector_id_semantic`: For natural language search
   - `vector_id_code`: For code-specific search (using Voyage AI)

### 3. Integration Registry

**Location**: `app/integrations/registry.py`

Central registry for discovering and instantiating adapters:

```python
# Register a new adapter
IntegrationRegistry.register("jira", JiraAdapter)

# Get an adapter instance
adapter = IntegrationRegistry.get_adapter(
    source_name="jira",
    team_id="T1234567",
    credentials={"api_token": "..."}
)

# List all available integrations
sources = IntegrationRegistry.list_available()
# Returns: ["github", "jira", "confluence", ...]

# Get capabilities
caps = IntegrationRegistry.get_capabilities("jira")
# Returns: {"supports_search": True, "supports_webhooks": True, ...}
```

### 4. Cross-Source Link Detection

**Location**: `app/services/cross_source_link_service.py`

Automatically detects and creates relationships between nodes:

```python
link_service = CrossSourceLinkService(db)

# Detect all links from a node
stats = await link_service.detect_and_create_links(
    node=github_pr_node,
    team_id=team_id
)
# Automatically finds:
# - Referenced issues (#123, JIRA-456)
# - Related PRs
# - Confluence pages
# - Slack discussions

# Create custom edge
edge = await link_service.create_edge(
    source_node_id="github:org/repo/pull/123",
    target_node_id="jira:PROJ-456",
    edge_type=EdgeType.IMPLEMENTS,
    team_id=team_id,
    confidence=0.9,
    evidence="PR description mentions 'implements PROJ-456'"
)

# Traverse the graph
outbound = await link_service.get_outbound_edges(node_id, team_id)
inbound = await link_service.get_inbound_edges(node_id, team_id)

# Multi-hop pathfinding
paths = await link_service.find_paths(
    start_node_id="slack:message123",
    end_node_id="github:org/repo/pull/456",
    team_id=team_id,
    max_depth=3
)
```

### 5. Graph Traversal and Reasoning

**Location**: `app/services/cross_source_graph_service.py`

Intelligent graph traversal for context expansion:

```python
graph_service = CrossSourceGraphService(db)

# Expand context from seed nodes (e.g., search results)
expansion = await graph_service.expand_context_from_seed_nodes(
    seed_node_ids=["github:org/repo/pull/123"],
    team_id=team_id,
    max_depth=2,
    max_nodes=50
)
# Returns:
# - Related issues
# - Slack discussions about the PR
# - Modified code files
# - Authors' other contributions
# - etc.

# Find related discussions
discussions = await graph_service.find_related_discussions(
    canonical_id="github:org/repo/pull/123",
    team_id=team_id,
    source_types=["slack"]  # Only Slack messages
)

# Explain relationship
explanation = await graph_service.explain_relationship(
    node_a_id="slack:message123",
    node_b_id="jira:PROJ-456",
    team_id=team_id
)
# Returns path: slack:message123 → REFERENCES → github:pr/789 → IMPLEMENTS → jira:PROJ-456

# Find experts
experts = await graph_service.find_experts(
    topic_node_ids=["github:org/repo/src/auth.py"],
    team_id=team_id
)
# Returns users who modified, discussed, or contributed to authentication code
```

### 6. Cross-Source Retrieval

**Location**: `app/services/cross_source_retrieval_service.py`

Unified search across all knowledge sources:

```python
retrieval_service = CrossSourceRetrievalService()

# Search across all sources
results = await retrieval_service.search_across_sources(
    query="authentication bug",
    team_id=team_id,
    db=db,
    sources=["slack", "github", "jira"],  # Optional filter
    expand_graph=True,  # Enable graph-based context expansion
    max_depth=2,
    top_k=20
)
# Returns:
# - Slack messages mentioning authentication bugs
# - GitHub issues and PRs
# - Jira tickets
# - Related code files
# - Reasoning paths explaining connections

# Find content related to a Slack message
related = await retrieval_service.find_related_to_slack_message(
    message_id="1234567890.123456",
    team_id=team_id,
    db=db,
    sources=["github", "jira"]
)

# Find topic experts
experts = await retrieval_service.find_topic_experts(
    query="authentication",
    team_id=team_id,
    db=db
)
```

## Adding a New Integration

### Step 1: Create Adapter

Create `app/integrations/jira_adapter.py`:

```python
from app.integrations.base_adapter import (
    BaseSourceAdapter,
    SourceCapabilities,
    UnifiedDocument,
    NodeType,
    EdgeType
)

class JiraAdapter(BaseSourceAdapter):
    def get_source_name(self) -> str:
        return "jira"

    def get_capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            supports_search=True,
            supports_incremental_sync=True,
            supports_webhooks=True,
            supports_full_text=True,
            supports_comments=True,
            link_patterns=[
                r'\b[A-Z]{2,10}-\d+\b',  # PROJ-123
                r'https?://[\w.-]+\.atlassian\.net/browse/[A-Z]+-\d+'
            ]
        )

    async def authenticate(self) -> bool:
        # Verify API credentials
        pass

    async def fetch_incremental(self, since: datetime, limit: int = None) -> List[UnifiedDocument]:
        # Fetch issues updated since timestamp
        issues = await self._fetch_jira_issues(since)

        return [
            UnifiedDocument(
                source="jira",
                source_id=issue['key'],
                canonical_id=f"jira:{issue['key']}",
                node_type=NodeType.JIRA_ISSUE,
                title=issue['fields']['summary'],
                content=issue['fields']['description'],
                url=f"https://company.atlassian.net/browse/{issue['key']}",
                author=issue['fields']['creator']['displayName'],
                created_at=parse_datetime(issue['fields']['created']),
                updated_at=parse_datetime(issue['fields']['updated']),
                tags=[label['name'] for label in issue['fields']['labels']],
                project_context={
                    'project_key': issue['fields']['project']['key'],
                    'project_name': issue['fields']['project']['name']
                }
            )
            for issue in issues
        ]

    async def search(self, query: str, filters: Dict = None, limit: int = 20):
        # JQL search
        pass

    async def detect_outbound_links(self, document: UnifiedDocument) -> List[Dict]:
        links = []
        content = f"{document.title} {document.content}"

        # Detect GitHub PR links
        github_pattern = r'https://github\.com/([\w-]+)/([\w-]+)/pull/(\d+)'
        for match in re.finditer(github_pattern, content):
            owner, repo, pr_num = match.groups()
            links.append({
                'target_source': 'github',
                'target_type': 'pull_request',
                'target_id': f"{owner}/{repo}/pull/{pr_num}",
                'confidence': 1.0,
                'evidence': 'GitHub PR URL found in description',
                'link_text': match.group(0)
            })

        # Detect Confluence links
        confluence_pattern = r'https://[\w.-]+\.atlassian\.net/wiki/spaces/(\w+)/pages/(\d+)'
        for match in re.finditer(confluence_pattern, content):
            space, page_id = match.groups()
            links.append({
                'target_source': 'confluence',
                'target_type': 'page',
                'target_id': f"{space}/{page_id}",
                'confidence': 1.0,
                'evidence': 'Confluence page URL found',
                'link_text': match.group(0)
            })

        return links
```

### Step 2: Register Adapter

Add to `app/integrations/adapters.py`:

```python
from app.integrations.jira_adapter import JiraAdapter

def register_all_adapters():
    IntegrationRegistry.register("github", GitHubAdapter)
    IntegrationRegistry.register("jira", JiraAdapter)  # ← Add this line
    # ...
```

### Step 3: That's It!

The adapter is now fully integrated:
- Unified search works automatically
- Link detection works automatically
- Graph traversal works automatically
- No core system changes needed

## Database Migration

Run the migration to create tables:

```bash
alembic upgrade head
```

This creates:
- `cross_source_nodes` table with 9 indexes
- `cross_source_edges` table with 8 indexes
- PostgreSQL ENUM types for type safety

## Usage Examples

### Example 1: Cross-Source Search

```python
# User asks: "What's the status of the authentication refactor?"

# System searches across all sources
results = await cross_source_retrieval.search_across_sources(
    query="authentication refactor",
    team_id=team_id,
    db=db,
    expand_graph=True
)

# Returns (automatically):
# 1. GitHub PR: "Refactor authentication module" (PULL_REQUEST)
# 2. Jira ticket: "AUTH-123: Modernize auth system" (JIRA_ISSUE)
# 3. Slack thread discussing the refactor (MESSAGE)
# 4. Modified code files (CODE_FILE)
# 5. Related issues that were fixed (ISSUE)

# With reasoning paths:
# - PR AUTH-refactor IMPLEMENTS JIRA-123
# - Slack message DISCUSSES PR AUTH-refactor
# - Code file auth.py MODIFIED_BY PR AUTH-refactor
```

### Example 2: Expert Finding

```python
# User asks: "Who knows about the payment system?"

experts = await cross_source_retrieval.find_topic_experts(
    query="payment system",
    team_id=team_id,
    db=db
)

# Returns:
# [
#   {
#     "author": "alice@company.com",
#     "contributions": 15,
#     "contribution_types": ["pull_request", "issue", "message"],
#     "expertise_score": 0.92
#   },
#   ...
# ]
```

### Example 3: Relationship Explanation

```python
# User clicks on Slack message about a bug

explanation = await cross_source_retrieval.explain_connection(
    node_a_id="slack:C123-1234567890.123456",
    node_b_id="github:org/repo/pull/789",
    team_id=team_id,
    db=db
)

# Returns:
# {
#   "connected": True,
#   "path_length": 2,
#   "confidence": 0.85,
#   "explanation": "message 'Bug in payment flow' REFERENCES issue 'Fix payment validation' FIXED_BY pull_request 'Add payment validation checks'"
# }
```

## Integration Capabilities

Different integrations support different capabilities:

| Integration | Search | Incremental Sync | Webhooks | Code Search | Comments |
|-------------|--------|------------------|----------|-------------|----------|
| Slack       | ✅     | ✅               | ✅       | ✅          | ✅       |
| GitHub      | ✅     | ✅               | ✅       | ✅          | ✅       |
| Jira        | ✅     | ✅               | ✅       | ❌          | ✅       |
| Confluence  | ✅     | ✅               | ✅       | ❌          | ✅       |
| Linear      | ✅     | ✅               | ✅       | ❌          | ✅       |
| Google Drive| ✅     | ✅               | ✅       | ❌          | ✅       |

## Future Integrations

The architecture is designed to easily support:

1. **Jira** - Issue tracking
2. **Confluence** - Documentation
3. **Linear** - Project management
4. **Google Drive** - Documents and files
5. **Notion** - Notes and wikis
6. **Asana** - Task management
7. **Figma** - Design files
8. **GitLab** - Code hosting
9. **Bitbucket** - Code hosting

Each requires only implementing the adapter interface - no core changes needed!

## Benefits

1. **Unified Intelligence**: AI sees across all knowledge sources simultaneously
2. **Extensibility**: New integrations in ~200 lines of code
3. **Graph-Based Reasoning**: Discover connections humans might miss
4. **Expert Finding**: Automatically identify who knows what
5. **Context Expansion**: Related content from all sources
6. **Relationship Explanation**: Transparent reasoning about connections

## Performance Optimizations

1. **Dual Embedding Strategy**:
   - Semantic embeddings (OpenAI) for natural language
   - Code embeddings (Voyage AI) for code-specific search

2. **Team-Specific Namespaces**: `{team_id}:{content_type}`
   - Prevents cross-team data leakage
   - Enables parallel queries per team

3. **Optimized Indexes**:
   - Composite indexes for common query patterns
   - GIN indexes for JSONB metadata searches
   - Covering indexes to avoid table lookups

4. **Graph Traversal Limits**:
   - Configurable max depth (default: 2-3 hops)
   - Configurable max nodes (default: 50)
   - Prevents runaway graph expansion

## Security

1. **Multi-Tenancy**: All queries filtered by `team_id`
2. **Permission Inheritance**: Respects source system permissions
3. **Soft Deletes**: `is_deleted` flag preserves referential integrity
4. **Credential Encryption**: Integration credentials encrypted at rest

## Monitoring

Track integration health via:
- `integration_config_service.get_integration_stats()`
- Node counts per source
- Edge counts
- Last sync timestamp
- Error rates

## Conclusion

This architecture transforms the AI from a Slack-only bot into a truly intelligent system that understands your entire knowledge ecosystem. Adding new integrations is trivial, and the graph-based reasoning enables insights that would be impossible with siloed systems.
