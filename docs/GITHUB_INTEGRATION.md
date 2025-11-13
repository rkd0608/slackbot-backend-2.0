# GitHub Integration Documentation

## Overview

The GitHub integration allows your Slack bot to index and search code from GitHub repositories with **permission-aware retrieval**. Users can search both Slack messages and GitHub code in a unified search experience.

## Features

- **OAuth-based Authentication**: Secure GitHub OAuth flow with encrypted token storage
- **Repository Indexing**: Index code files from public and private repositories
- **Dual Embedding Strategy**: Code-optimized (Voyage AI) + semantic (OpenAI) embeddings
- **Permission-Aware Search**: Users only see code from repositories they have access to
- **Hybrid Search**: Combines code structure matching with semantic understanding
- **Background Processing**: Async repository indexing with progress tracking

## Architecture

### Core Components

1. **OAuth Service** (`app/services/github_oauth_service.py`)
   - Handles GitHub OAuth flow
   - Encrypts and stores access tokens using AES-256-GCM
   - Manages user connections

2. **Indexing Service** (`app/services/github_indexing_service.py`)
   - Crawls repositories and extracts code files
   - Filters by supported file extensions
   - Tracks permissions (public/private, accessible users)
   - Stores content in `ExternalContent` model

3. **Embedding Service** (`app/services/github_code_embedding_service.py`)
   - Generates dual embeddings for each code file:
     - **Code embedding** (Voyage AI `voyage-code-2`): Optimized for code structure
     - **Semantic embedding** (OpenAI): Captures intent and meaning
   - Stores in Pinecone with team-specific namespaces

4. **Retrieval Service** (`app/services/github_retrieval_service.py`)
   - Permission-aware search
   - Hybrid search (weighted combination of code + semantic)
   - Filters results based on user's GitHub access

5. **Main Retrieval Integration** (`app/services/retrieval_service.py`)
   - Integrates GitHub code search into main search pipeline
   - Combines Slack messages, files, code snippets, and GitHub code

## Database Schema

### ExternalContent Model
Stores indexed GitHub code files with permission metadata:

```python
- id: UUID
- team_id: Workspace ID
- source_type: "github"
- source_id: Unique identifier (e.g., "github:owner/repo:path")
- source_url: GitHub URL
- title: File name
- content: Full code content
- content_type: "code"
- language: Programming language
- author: Repository owner
- repository_id: GitHub repository ID
- repository_full_name: "owner/repo"
- repository_visibility: "public" or "private"
- visibility: "public" or "private"
- accessible_by_user_ids: Array of GitHub user IDs with access
- vector_id: Pinecone vector ID (code embedding)
- vector_id_semantic: Pinecone vector ID (semantic embedding)
```

### UserIntegrationConnection Model
Stores encrypted GitHub OAuth tokens:

```python
- id: UUID
- user_id: Slack user ID
- team_id: Workspace ID
- integration_type: "github"
- external_user_id: GitHub user ID
- access_token_encrypted: AES-256-GCM encrypted token
- scopes: OAuth scopes granted
- is_active: Boolean
```

## Setup Instructions

### 1. Create GitHub OAuth App

1. Go to GitHub → Settings → Developer settings → OAuth Apps
2. Click "New OAuth App"
3. Fill in:
   - Application name: `[Your App Name] - Slack Bot`
   - Homepage URL: Your app URL
   - Authorization callback URL: `https://your-domain.com/oauth/github/callback`
4. Save Client ID and Client Secret

### 2. Configure Environment Variables

Add to `.env`:

```bash
# GitHub OAuth Configuration
GITHUB_CLIENT_ID=your-github-oauth-app-client-id
GITHUB_CLIENT_SECRET=your-github-oauth-app-client-secret
GITHUB_OAUTH_REDIRECT_URI=https://your-domain.com/oauth/github/callback

# GitHub Pinecone Index
PINECONE_GITHUB_INDEX_NAME=github-code-embeddings

# Token Encryption Key (generate with command below)
OAUTH_TOKEN_ENCRYPTION_KEY=<generate-32-byte-key>
```

**Generate Encryption Key:**
```bash
python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode('utf-8'))"
```

### 3. Create Pinecone Index

```bash
python scripts/setup_github_pinecone_index.py
```

This creates a Pinecone index with:
- Dimension: 1536 (OpenAI embedding size)
- Metric: cosine
- Namespaces: `{team_id}:code` and `{team_id}:semantic`

### 4. Run Database Migrations

```bash
alembic upgrade head
```

This creates the required tables: `integrations`, `user_integration_connections`, `external_contents`, `integration_sync_jobs`.

## API Endpoints

### GitHub OAuth

#### 1. Initiate OAuth Flow
```
GET /oauth/github/authorize
```

Headers:
- `Authorization: Bearer <slack-user-token>`

Response:
```json
{
  "authorization_url": "https://github.com/login/oauth/authorize?...",
  "state": "<csrf-token>"
}
```

#### 2. OAuth Callback (handled automatically)
```
GET /oauth/github/callback?code=<code>&state=<state>
```

#### 3. Check Connection Status
```
GET /oauth/github/status
```

Response:
```json
{
  "connected": true,
  "github_user": {
    "login": "username",
    "id": 12345,
    "avatar_url": "..."
  },
  "scopes": ["repo", "read:user"],
  "connected_at": "2025-10-20T12:00:00Z"
}
```

#### 4. Disconnect GitHub
```
POST /oauth/github/disconnect
```

### Repository Indexing

#### 1. List Accessible Repositories
```
GET /api/v1/github/repositories
```

Response:
```json
{
  "repositories": [
    {
      "full_name": "owner/repo",
      "description": "Repository description",
      "language": "Python",
      "visibility": "private",
      "stars": 42,
      "forks": 7,
      "updated_at": "2025-10-20T12:00:00Z"
    }
  ],
  "total": 15
}
```

#### 2. List Indexed Repositories
```
GET /api/v1/github/indexed-repositories
```

Response:
```json
{
  "repositories": [
    {
      "full_name": "owner/repo",
      "repository_id": "R_12345",
      "visibility": "private",
      "file_count": 127,
      "last_indexed_at": "2025-10-20T12:00:00Z"
    }
  ],
  "total": 3
}
```

#### 3. Index a Repository
```
POST /api/v1/github/index-repository
```

Request:
```json
{
  "owner": "facebook",
  "repo": "react",
  "generate_embeddings": true
}
```

Response:
```json
{
  "sync_job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Started indexing facebook/react"
}
```

#### 4. Check Sync Job Status
```
GET /api/v1/github/sync-jobs/{job_id}
```

Response:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "integration_type": "github",
  "sync_type": "full",
  "target_resource": "repo:facebook/react",
  "total_items": 500,
  "processed_items": 500,
  "failed_items": 0,
  "items_created": 450,
  "items_updated": 50,
  "started_at": "2025-10-20T12:00:00Z",
  "completed_at": "2025-10-20T12:15:00Z",
  "error_message": null
}
```

#### 5. Generate Embeddings for Indexed Files
```
POST /api/v1/github/generate-embeddings
```

Request (optional):
```json
{
  "limit": 100
}
```

Response:
```json
{
  "message": "Started generating embeddings",
  "files_to_process": 450
}
```

## Search Integration

GitHub code is automatically included in search results when:

1. **Code Intelligence is enabled**: `ENABLE_CODE_INTELLIGENCE=true`
2. **User has GitHub connection**: Active OAuth connection exists
3. **Search query is executed**: Any semantic search will include GitHub results

### Search Flow

1. User asks question in Slack
2. Bot analyzes query intent
3. **Parallel retrieval** executes:
   - Slack messages (vector search)
   - Slack files (conditional)
   - Slack code snippets (if code intent detected)
   - **GitHub code** (permission-filtered hybrid search)
4. Results are combined using Reciprocal Rank Fusion
5. Reranked and returned to user

### Permission Filtering

GitHub search respects repository permissions:

- **Public repositories**: Accessible to all users
- **Private repositories**: Only accessible to users with GitHub connection whose `external_user_id` is in `accessible_by_user_ids`

The permission check happens at two levels:
1. **Pinecone query**: Filters by `repository_visibility` and `team_id`
2. **Database verification**: Double-checks access using `accessible_by_user_ids`

## Supported File Types

The indexing service supports these file extensions:

**Code:**
- `.py`, `.js`, `.ts`, `.jsx`, `.tsx`
- `.java`, `.go`, `.rs`, `.cpp`, `.c`, `.h`, `.hpp`
- `.cs`, `.rb`, `.php`, `.swift`, `.kt`, `.scala`

**Config/Data:**
- `.sql`, `.sh`, `.yaml`, `.yml`, `.json`

**Documentation:**
- `.md`, `.txt`, `.rst`

**Limitations:**
- Maximum file size: 1MB
- Skips: `node_modules`, `.git`, `__pycache__`, `.venv`, `venv`, `dist`, `build`, `.next`, `.cache`, `vendor`, `target`

## Embedding Strategy

### Dual Embedding Approach

1. **Code Embedding (Voyage AI)**
   - Model: `voyage-code-2`
   - Optimized for code structure and syntax
   - Better at finding similar code patterns
   - Stored in namespace: `{team_id}:code`

2. **Semantic Embedding (OpenAI)**
   - Model: `text-embedding-3-small` (or configured model)
   - Captures intent and meaning
   - Better at understanding what code does
   - Stored in namespace: `{team_id}:semantic`

### Hybrid Search

When searching, both embeddings are queried and results are combined with weights:

```python
code_weight = 0.6  # Favor code structure matching
semantic_weight = 0.4  # Include semantic understanding

combined_score = (code_score * 0.6) + (semantic_score * 0.4)
```

## Monitoring & Logs

### Key Log Events

**OAuth Flow:**
- `github_oauth_initiated`
- `github_oauth_token_exchanged`
- `github_connection_stored`

**Indexing:**
- `github_repo_indexing_started`
- `github_code_files_found`
- `github_file_indexed`
- `github_repo_indexing_completed`

**Embedding Generation:**
- `github_content_embedding_started`
- `github_content_embedded_successfully`
- `github_batch_embedding_completed`

**Search:**
- `github_code_search_completed`
- `permission_denied_skipping_result`

## Security Considerations

1. **Token Encryption**: OAuth tokens are encrypted using AES-256-GCM
2. **Permission Filtering**: Double-check at Pinecone and database levels
3. **Team Isolation**: Results filtered by `team_id`
4. **CSRF Protection**: OAuth state parameter prevents CSRF attacks
5. **Scope Management**: Only request necessary GitHub scopes

## Troubleshooting

### No GitHub results in search

**Check:**
1. User has active GitHub connection: `GET /oauth/github/status`
2. Repository is indexed: `GET /api/v1/github/indexed-repositories`
3. Embeddings are generated: Check `vector_id` is not null in database
4. Code intelligence is enabled: `ENABLE_CODE_INTELLIGENCE=true`

### Permission errors

**Check:**
1. User's `external_user_id` matches GitHub user ID
2. `accessible_by_user_ids` array includes user's GitHub ID
3. Token is not expired: Re-authorize if needed

### Indexing failures

**Check:**
1. GitHub token has correct scopes (`repo` for private repos)
2. Rate limits: GitHub API has rate limits
3. File size: Files >1MB are skipped
4. Sync job logs: Check `error_message` in sync job

## API Rate Limits

- **GitHub API**: 5,000 requests/hour (authenticated)
- **Voyage AI**: Check your plan limits
- **OpenAI**: Check your tier limits

Consider implementing:
- Incremental syncs (only changed files)
- Caching of repository metadata
- Rate limit backoff strategies

## Future Enhancements

- [ ] Incremental repository syncs (only changed files)
- [ ] Support for more file types (images, Jupyter notebooks)
- [ ] Code-aware chunking for large files
- [ ] Integration with GitHub webhooks for real-time updates
- [ ] Support for GitLab and Bitbucket
- [ ] Code search filters (language, repository, date range)
- [ ] Syntax highlighting in search results
