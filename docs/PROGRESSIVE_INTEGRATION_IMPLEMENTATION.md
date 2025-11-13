# Progressive Integration System - Implementation Summary

## Overview

This document describes the implementation of a scalable progressive integration system that allows users to:
- Install Slack first, use the bot immediately after indexing
- Optionally add GitHub during onboarding or later
- Maintain bot functionality while adding new integrations
- Resume from checkpoints on failure
- Track progress in real-time

## System Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────────┐
│                     User Installs Slack App                      │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │  OAuth Callback            │
        │  - Creates Workspace       │
        │  - Creates User            │
        └────────────┬───────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │  Integration Orchestrator  │
        │  - Creates IntegrationStatus│
        │  - Queues indexing job     │
        └────────────┬───────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │  Initial Indexing Worker   │
        │  - Indexes channels/msgs   │
        │  - Saves checkpoints       │
        │  - Updates progress        │
        └────────────┬───────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │  Orchestrator Notified     │
        │  - Checks all sources      │
        │  - Triggers graph if ready │
        │  - Sends notification      │
        └────────────┬───────────────┘
                     │
                     ▼ (if GitHub connected)
        ┌────────────────────────────┐
        │  Graph Building Worker     │
        │  - Builds cross-source     │
        │  - Links Slack↔GitHub      │
        │  - User identity mapping   │
        └────────────┬───────────────┘
                     │
                     ▼
        ┌────────────────────────────┐
        │  Bot Fully Ready           │
        │  - All features available  │
        └────────────────────────────┘
```

## Database Schema

### New Tables

#### 1. integration_status
Tracks per-source indexing progress.

```sql
CREATE TABLE integration_status (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    team_id VARCHAR(100) NOT NULL,
    source_type VARCHAR(50) NOT NULL,  -- 'slack', 'github', etc.

    -- Connection
    is_connected VARCHAR(10) DEFAULT 'false',
    connected_at DATETIME,

    -- Indexing
    indexing_status VARCHAR(50) DEFAULT 'not_started',
    -- Status: 'not_started', 'pending', 'in_progress', 'complete', 'failed'
    indexing_started_at DATETIME,
    indexing_completed_at DATETIME,
    indexing_failed_at DATETIME,
    indexing_error TEXT,

    -- Progress
    total_items BIGINT DEFAULT 0,
    items_indexed BIGINT DEFAULT 0,
    items_failed BIGINT DEFAULT 0,
    progress_percentage FLOAT DEFAULT 0.0,

    -- Checkpoint for resumability
    last_checkpoint JSON,  -- {"last_channel_id": "C123", "timestamp": "..."}

    -- Stats
    entities_extracted BIGINT DEFAULT 0,
    messages_indexed BIGINT DEFAULT 0,
    files_indexed BIGINT DEFAULT 0,
    code_snippets_indexed BIGINT DEFAULT 0,

    -- Sync
    last_full_sync DATETIME,
    last_incremental_sync DATETIME,
    next_scheduled_sync DATETIME,

    -- Configuration
    config JSON,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY idx_team_source_unique (team_id, source_type)
);
```

#### 2. cross_source_graph_status
Tracks cross-source graph building.

```sql
CREATE TABLE cross_source_graph_status (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    team_id VARCHAR(100) UNIQUE NOT NULL,

    -- Graph status
    graph_status VARCHAR(50) DEFAULT 'not_started',
    -- Status: 'not_started', 'pending', 'in_progress', 'complete', 'failed'
    graph_building_started_at DATETIME,
    graph_building_completed_at DATETIME,

    -- Progress
    total_edges_to_build BIGINT DEFAULT 0,
    edges_built BIGINT DEFAULT 0,
    edges_failed BIGINT DEFAULT 0,

    -- Edge statistics
    slack_to_github_edges BIGINT DEFAULT 0,
    github_to_slack_edges BIGINT DEFAULT 0,
    user_identity_links BIGINT DEFAULT 0,

    -- Rebuild tracking
    last_full_rebuild DATETIME,
    last_incremental_update DATETIME,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP
);
```

#### 3. indexing_jobs
Job tracking for observability.

```sql
CREATE TABLE indexing_jobs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    job_id VARCHAR(100) UNIQUE NOT NULL,
    team_id VARCHAR(100) NOT NULL,
    source_type VARCHAR(50) NOT NULL,

    -- Job details
    job_type VARCHAR(50) NOT NULL,  -- 'initial_index', 'incremental_sync', 'graph_build'
    status VARCHAR(50) DEFAULT 'queued',
    priority INT DEFAULT 5,

    -- Progress
    total_items BIGINT DEFAULT 0,
    items_processed BIGINT DEFAULT 0,
    items_failed BIGINT DEFAULT 0,

    -- Data
    job_data JSON,
    result JSON,
    error_message TEXT,

    -- Timing
    queued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME,
    completed_at DATETIME,
    failed_at DATETIME,

    -- Worker info
    worker_id VARCHAR(100),
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP
);
```

## Key Services

### 1. IntegrationOrchestrator
**File**: `app/services/integration_orchestrator.py`

**Responsibilities**:
- Coordinate multi-source indexing workflows
- Queue jobs for workers
- Check when all sources are ready
- Trigger graph building
- Send notifications at milestones

**Key Methods**:
```python
async def handle_slack_installation(team_id, bot_token, workspace)
    # Creates IntegrationStatus, queues Slack indexing

async def handle_github_connection(team_id, github_access_token, selected_repos)
    # Creates IntegrationStatus for GitHub, queues indexing

async def handle_indexing_complete(team_id, source_type, result)
    # Called by workers when indexing completes
    # Checks if all sources ready → triggers graph building

async def handle_graph_building_complete(team_id, result)
    # Called when graph building completes
    # Sends final notification
```

### 2. BotReadinessService
**File**: `app/services/bot_readiness_service.py`

**Responsibilities**:
- Determine when bot can answer queries
- Provide user-friendly status messages
- Check query-type specific readiness

**Key Methods**:
```python
async def is_bot_ready(team_id, query_type=None) -> bool
    # Returns True only if 100% indexed
    # query_type: 'slack_search', 'code_search', 'cross_source'

async def get_readiness_status(team_id) -> Dict
    # Returns comprehensive status with available/unavailable features

async def get_not_ready_message(team_id) -> str
    # Returns user-friendly message explaining status
```

**Integrated into**: `app/services/bot_interaction.py:184-212`

### 3. CrossSourceLinkDetector
**File**: `app/services/cross_source_link_detector.py`

**Responsibilities**:
- Detect relationships between Slack and GitHub nodes
- Multiple detection methods:
  - Explicit links (URLs, refs)
  - Semantic similarity
  - Person-to-author mapping
  - Project mentions
  - Temporal co-mentions

**Used by**: `GraphBuildingWorker`

## Workers

### 1. InitialIndexingWorker (Enhanced)
**File**: `app/workers/initial_indexing.py`

**New Features**:
- ✅ Checkpoint system (saves every 100 messages)
- ✅ Resume from checkpoint on failure
- ✅ Real-time progress tracking
- ✅ Updates `IntegrationStatus` throughout
- ✅ Notifies orchestrator on completion

**Checkpoint Format**:
```json
{
  "last_channel_id": "C123ABC",
  "timestamp": "2025-01-30T12:34:56"
}
```

**Key Changes**:
- Lines 42-71: Load checkpoint and resume
- Lines 204-212: Estimate total items upfront
- Lines 225-237: Skip channels until checkpoint reached
- Lines 271-278: Index with checkpoint tracking
- Lines 283-303: Save checkpoint after each channel
- Lines 320-377: Update status and notify orchestrator
- Lines 386-423: Preserve checkpoint on failure

### 2. GraphBuildingWorker (New)
**File**: `app/workers/graph_building.py`

**Responsibilities**:
- Build cross-source relationships after indexing complete
- Process nodes in batches
- Save checkpoints every 100 nodes
- Update graph statistics

**Process**:
1. Verify all sources indexed
2. Count nodes by source
3. Process in batches of 50 nodes
4. Run link detection on each node
5. Save checkpoint every 100 nodes
6. Count final edge statistics
7. Notify orchestrator

**Link Types Created**:
- Explicit (URL references)
- Semantic (similar content)
- Person (Slack user → GitHub author)
- Project (repo mentions)
- Temporal (co-mentions in time window)

## API Endpoints

### Integration Status APIs
**File**: `app/api/integration_status.py`

#### GET /api/v1/integration/status
Get comprehensive integration status.

**Response**:
```json
{
  "team_id": "T123",
  "overall_status": "in_progress",
  "overall_progress": {
    "percentage": 65.5,
    "phase": "indexing"
  },
  "sources": {
    "slack": {
      "is_connected": true,
      "indexing_status": "complete",
      "progress_percentage": 100.0,
      "messages_indexed": 45000
    },
    "github": {
      "is_connected": true,
      "indexing_status": "in_progress",
      "progress_percentage": 31.0,
      "files_indexed": 1250
    }
  },
  "graph": {
    "graph_status": "pending",
    "edges_built": 0
  },
  "bot_ready": false,
  "bot_phase": "github_indexing",
  "available_features": ["Slack message search", "Team discussions"],
  "unavailable_features": ["Code search (indexing...)", "Cross-source queries (pending)"],
  "status_message": "✅ Bot ready for Slack queries. Code search coming soon!",
  "setup_progress": {
    "slack": {
      "status": "complete",
      "progress": 100.0,
      "messages_indexed": 45000,
      "complete": true
    },
    "github": {
      "status": "in_progress",
      "progress": 31.0,
      "files_indexed": 1250,
      "complete": false
    }
  }
}
```

#### GET /api/v1/integration/readiness
Check bot readiness.

**Response**:
```json
{
  "team_id": "T123",
  "is_ready": false,
  "available_query_types": ["slack_search"],
  "message": "⚡ **GitHub integration in progress**\n\nYour bot is ready for Slack questions!\nCode search: 31% indexed\n\n*Full features coming soon!*",
  "capabilities": {
    "slack_search": true,
    "code_search": false,
    "cross_source_queries": false
  }
}
```

#### GET /api/v1/integration/progress/{source_type}
Get detailed progress for a source.

**Response**:
```json
{
  "team_id": "T123",
  "source_type": "github",
  "is_connected": true,
  "connected_at": "2025-01-30T10:15:00",
  "indexing_status": "in_progress",
  "indexing_started_at": "2025-01-30T10:16:00",
  "progress_percentage": 31.0,
  "total_items": 4000,
  "items_indexed": 1250,
  "items_failed": 2,
  "files_indexed": 1250,
  "eta_minutes": 35,
  "last_checkpoint": {
    "last_repo": "owner/repo-name",
    "timestamp": "2025-01-30T10:45:00"
  }
}
```

#### GET /api/v1/integration/graph/status
Get graph building status.

**Response**:
```json
{
  "team_id": "T123",
  "graph_status": "in_progress",
  "building_started_at": "2025-01-30T11:00:00",
  "progress_percentage": 45.0,
  "total_edges_to_build": 2000,
  "edges_built": 900,
  "edge_statistics": {
    "slack_to_github": 450,
    "github_to_slack": 380,
    "user_identity_links": 70
  }
}
```

#### POST /api/v1/integration/retry/{source_type}
Retry failed indexing (admin only).

**Request**:
```json
{
  "source_type": "github"
}
```

**Response**:
```json
{
  "success": true,
  "message": "Retry queued for github indexing",
  "job_id": "job_abc123",
  "will_resume_from_checkpoint": true,
  "checkpoint": {
    "last_repo": "owner/repo-name",
    "timestamp": "2025-01-30T10:45:00"
  }
}
```

### Onboarding APIs (Enhanced)
**File**: `app/api/onboarding.py`

#### GET /api/v1/onboarding/integration-choice
Get integration options during onboarding.

**Response**:
```json
{
  "team_id": "T123",
  "team_name": "Acme Corp",
  "integrations": [
    {
      "type": "slack",
      "name": "Slack",
      "status": "connected",
      "description": "Your Slack workspace is already connected",
      "icon": "slack",
      "required": true
    },
    {
      "type": "github",
      "name": "GitHub",
      "status": "optional",
      "description": "Connect GitHub to search code, PRs, and issues alongside Slack conversations",
      "icon": "github",
      "required": false,
      "benefits": [
        "Search code repositories",
        "Link Slack discussions to GitHub PRs",
        "Find who worked on what",
        "Track code changes in context"
      ]
    }
  ],
  "recommendation": {
    "connect_now": true,
    "reason": "Connecting GitHub now enables full cross-source intelligence from the start"
  }
}
```

#### POST /api/v1/onboarding/integration-choice
Submit integration choice.

**Request**:
```json
{
  "team_id": "T123",
  "connect_github": true
}
```

**Response (if connect_github=true)**:
```json
{
  "success": true,
  "action": "github_oauth",
  "message": "Redirecting to GitHub authorization...",
  "next_step": {
    "action": "github_oauth",
    "url": "/oauth/github/authorize",
    "description": "Connect your GitHub account"
  }
}
```

**Response (if connect_github=false)**:
```json
{
  "success": true,
  "action": "complete",
  "message": "Setup complete! Your bot is indexing Slack history.",
  "next_step": {
    "action": "dashboard",
    "url": "/dashboard?team_id=T123",
    "description": "View your workspace dashboard"
  },
  "note": "You can connect GitHub anytime from Settings"
}
```

## User Flows

### Flow 1: Slack-Only Installation
```
1. User installs Slack app
2. OAuth completes → workspace created
3. Complete profile
4. Integration choice: Skip GitHub
5. Redirected to dashboard
6. Slack indexing runs in background
7. User receives DM when ready: "Bot ready for Slack queries!"
8. Bot available for Slack-only queries
```

### Flow 2: Slack + GitHub During Onboarding
```
1. User installs Slack app
2. OAuth completes → workspace created
3. Complete profile
4. Integration choice: Connect GitHub
5. Redirected to GitHub OAuth
6. GitHub OAuth completes
7. Both indexing jobs queued
8. Slack indexing completes first
9. User receives DM: "Bot ready for Slack queries. Code search coming soon!"
10. GitHub indexing completes
11. Graph building starts automatically
12. User receives DM: "Full integration complete! All features available."
13. Bot available for all query types
```

### Flow 3: Add GitHub Later
```
1. User already using bot (Slack-only)
2. User goes to Settings → Integrations
3. Clicks "Connect GitHub"
4. GitHub OAuth flow
5. GitHub indexing starts
6. Bot remains available for Slack queries
7. User receives DM: "Bot ready for Slack queries. GitHub indexing: 45%"
8. GitHub indexing completes
9. Graph building starts
10. User receives DM: "GitHub integration complete! Cross-source queries now available."
11. Bot now has full capabilities
```

## Notification Strategy

### When to Notify Users

**1. Indexing Started** (Slack installation)
```
🚀 **Indexing Started**

We're indexing your Slack workspace history.

• Estimated time: ~30 minutes
• You can start using the bot now with recent messages
• You'll be notified when complete

_Indexing 15,000 messages across 50 channels_
```

**2. Slack Indexing Complete** (Slack-only or first to complete)
```
🎉 **Workspace Indexing Complete!**

Your Slack workspace is now fully indexed and ready to use.

**Indexed:**
• Channels: 50
• Messages: 15,234

**Get Started:**
• Use `/ask` to ask questions
• Use `/find` to search messages
• Mention me for quick queries
```

**3. GitHub Indexing Started** (if added later)
```
⚡ **GitHub Integration in Progress**

Your bot is ready for Slack questions!
• Slack: 100% complete
• GitHub: Starting...
• I'll notify you when both are ready!

_You can keep using the bot for Slack queries while GitHub indexes._
```

**4. GitHub Indexing Complete**
```
✨ **GitHub Integration Complete**

All your repositories have been indexed!

**Indexed:**
• Repositories: 12
• Code files: 3,456
• Building Slack ↔ GitHub connections...

**Try:**
`/ask find PRs related to authentication bug`
```

**5. Graph Building Complete**
```
🎉 **Full Integration Complete!**

Your intelligent Slack bot is fully powered up!

✓ Slack history indexed
✓ GitHub repositories indexed
✓ Cross-source connections built

**New capabilities:**
• Find code discussions from Slack
• Link GitHub PRs to team conversations
• Track who's working on what

**Try:**
`/ask show conversations about the auth PR`
```

## Testing Guide

### Test Scenarios

#### Scenario 1: New Slack-Only Installation
**Steps**:
1. Install Slack app via OAuth
2. Complete profile
3. Skip GitHub during onboarding
4. Wait for Slack indexing to complete
5. Verify bot ready for Slack queries
6. Try querying bot

**Expected**:
- `GET /api/v1/integration/status` shows Slack indexing progress
- `GET /api/v1/integration/readiness` returns `is_ready: false` during indexing
- User receives DM when complete
- `GET /api/v1/integration/readiness` returns `is_ready: true` after completion
- Bot responds to Slack queries

#### Scenario 2: Slack + GitHub During Onboarding
**Steps**:
1. Install Slack app
2. Complete profile
3. Connect GitHub during onboarding
4. Complete GitHub OAuth
5. Monitor both indexing jobs
6. Wait for graph building

**Expected**:
- Two indexing jobs queued (Slack and GitHub)
- Progress tracked independently
- Bot becomes ready after Slack completes
- Graph building starts after both complete
- Final notification when graph complete
- All query types available

#### Scenario 3: Add GitHub After Using Slack-Only
**Steps**:
1. Already have Slack-only workspace
2. Bot is working for Slack queries
3. Go to Settings → Connect GitHub
4. Complete GitHub OAuth
5. Continue using bot during GitHub indexing

**Expected**:
- Bot remains responsive for Slack queries
- GitHub indexing doesn't block bot
- Status shows: slack_search available, code_search pending
- After GitHub completes, all features available

#### Scenario 4: Indexing Failure and Resume
**Steps**:
1. Start indexing
2. Kill worker mid-indexing
3. Check checkpoint saved
4. Restart worker
5. Verify resume from checkpoint

**Expected**:
- Last checkpoint saved in `integration_status.last_checkpoint`
- Worker resumes from checkpoint
- No duplicate indexing
- Progress continues from where it left off

#### Scenario 5: Concurrent Installations
**Steps**:
1. Install multiple workspaces simultaneously
2. Monitor queue and worker behavior
3. Verify each workspace tracked independently

**Expected**:
- Each workspace has separate IntegrationStatus
- Jobs processed in priority order
- No cross-contamination between workspaces
- Progress tracked correctly per workspace

### API Testing Examples

```bash
# Check integration status
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/integration/status

# Check bot readiness
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/integration/readiness

# Get Slack progress
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/integration/progress/slack

# Get GitHub progress
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/integration/progress/github

# Get graph status
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/integration/graph/status

# Retry failed indexing (admin only)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/integration/retry/github
```

## Monitoring & Observability

### Logs to Monitor

```
# Indexing started
initial_indexing_started team_id=T123

# Checkpoint saved
checkpoint_saved_within_channel team_id=T123 channel_id=C456 messages_processed=100

# Channel indexed
channel_indexed_checkpoint_saved team_id=T123 channel=general messages=1250 progress=45.5

# Indexing completed
initial_indexing_completed team_id=T123 channels=50 messages=15234

# Orchestrator notified
orchestrator_notified team_id=T123 source_type=slack

# Graph building started
graph_building_started team_id=T123

# Graph checkpoint
graph_building_checkpoint team_id=T123 nodes_processed=500 edges_created=1250

# Graph completed
graph_building_completed team_id=T123 nodes_processed=2000 edges_created=3500
```

### Metrics to Track

1. **Indexing Speed**
   - Messages per minute
   - Files per minute
   - Time to completion by workspace size

2. **Checkpoint Effectiveness**
   - Resume success rate
   - Time saved on resume vs full re-index

3. **Graph Building**
   - Edges per minute
   - Time to build graph by node count

4. **Bot Readiness**
   - Time from installation to bot ready
   - Percentage of users adding GitHub during onboarding vs later

## Migration Guide

### For Existing Workspaces

If you have existing workspaces that were indexed before this system:

1. **Create IntegrationStatus records**:
```sql
INSERT INTO integration_status (team_id, source_type, is_connected, indexing_status, progress_percentage, messages_indexed, indexing_completed_at)
SELECT
    team_id,
    'slack' as source_type,
    'true' as is_connected,
    'complete' as indexing_status,
    100.0 as progress_percentage,
    total_messages_indexed as messages_indexed,
    indexing_completed_at
FROM workspaces
WHERE indexing_status = 'complete';
```

2. **Create CrossSourceGraph records** (if applicable):
```sql
INSERT INTO cross_source_graph_status (team_id, graph_status)
SELECT DISTINCT team_id, 'not_started' as graph_status
FROM workspaces;
```

3. **Trigger graph building** for workspaces with both sources:
```python
# Run this script to queue graph building for existing workspaces
from app.services.integration_orchestrator import get_integration_orchestrator

async def migrate_existing_workspaces():
    async for db in db_manager.get_session():
        orchestrator = get_integration_orchestrator(db)

        # Find workspaces with both Slack and GitHub indexed
        workspaces_to_migrate = await find_workspaces_with_both_sources(db)

        for team_id in workspaces_to_migrate:
            await orchestrator._queue_graph_building_job(team_id)
            logger.info(f"Graph building queued for {team_id}")
```

## Future Enhancements

### Planned Features

1. **Incremental Sync Jobs**
   - Daily incremental updates
   - Only process new/changed content
   - Maintain freshness without full re-index

2. **Selective Indexing**
   - Let users choose which channels to index
   - Let users choose which repos to index
   - Exclude sensitive content

3. **Parallel Indexing**
   - Process multiple channels simultaneously
   - Configurable worker count
   - Faster completion for large workspaces

4. **More Integrations**
   - Jira
   - Confluence
   - Linear
   - Notion

5. **Admin Dashboard**
   - Real-time progress visualization
   - Manual trigger controls
   - Error investigation tools

6. **Webhooks**
   - Real-time updates from Slack
   - Real-time updates from GitHub
   - Instant indexing of new content

## Troubleshooting

### Common Issues

**Issue**: Indexing stuck at certain percentage
- **Check**: Look for errors in worker logs
- **Fix**: Check if checkpoint is saved, retry from checkpoint

**Issue**: Bot not ready even after indexing complete
- **Check**: Verify `integration_status.indexing_status = 'complete'`
- **Fix**: Manually update status if needed

**Issue**: Graph building never starts
- **Check**: Verify both sources have `indexing_status = 'complete'`
- **Fix**: Manually trigger graph building via orchestrator

**Issue**: Checkpoint not resuming
- **Check**: Verify `last_checkpoint` JSON is valid
- **Fix**: Clear checkpoint and restart full indexing

## Support

For issues or questions:
- GitHub Issues: [Link to repo]
- Slack Channel: #bot-support
- Email: support@example.com
