# Eunoia API Documentation

Complete API reference for authentication, onboarding, workspace management, and analytics dashboards.

## Base URL
```
https://3154576e2440.ngrok-free.app           # Local Development
https://api.eunoia.ai           # Production
```

## Table of Contents
1. [Authentication](#authentication)
2. [Onboarding](#onboarding)
3. [Workspace Management](#workspace-management)
4. [GitHub Integration](#github-integration)
   - [OAuth Flow](#github-oauth-flow)
   - [Repository Management](#repository-management)
   - [Onboarding Integration](#github-during-onboarding)
   - [Settings Integration](#github-in-settings)
5. [Analytics Dashboards](#analytics-dashboards)
   - [Home Dashboard](#home-dashboard-analytics)
   - [Analytics & Usage Insights](#analytics--usage-insights)
6. [Admin Dashboard](#admin-dashboard)

---

## Authentication

### Login
**`POST /api/v1/auth/login`**

Login for returning users who have completed onboarding.

**Request:**
```json
{
  "email": "user@company.com",
  "password": "securePassword123"
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

### Refresh Token
**`POST /api/v1/auth/refresh`**

Get new access token using refresh token.

**Request:**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

### Get Current User
**`GET /api/v1/auth/me`**

Get authenticated user information.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "user_id": "U123ABC456",
  "email": "user@company.com",
  "display_name": "John Doe",
  "team_id": "T123ABC456",
  "role": "admin",
  "is_admin": true,
  "last_login_at": "2025-10-12T10:00:00Z"
}
```

---

## Onboarding

### Slack OAuth
**`GET /oauth/install`**

Redirects user to Slack authorization page.

**Response:**
- `302 Redirect` to Slack OAuth page

---

### OAuth Callback
**`GET /oauth/callback`**

Slack redirects here after authorization (automatic).

**Query Parameters:**
- `code` - Authorization code from Slack
- `state` - CSRF protection token

**Response:**
- `302 Redirect` to:
  - `/onboarding/profile?team_id=T123ABC&user_id=U123ABC` (new user)
  - `/login?message=reinstall&team_id=T123ABC` (existing user)

---

### Complete Profile
**`POST /api/v1/onboarding/complete-profile`**

Complete user profile after Slack OAuth.

**Request:**
```json
{
  "team_id": "T123ABC456",
  "first_name": "John",
  "last_name": "Doe",
  "password": "securePassword123",
  "company_name": "Acme Corp",
  "company_size": "11-50"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Profile completed successfully",
  "user": {
    "user_id": "U123ABC456",
    "email": "john@company.com",
    "display_name": "John Doe",
    "role": "admin",
    "is_admin": true
  },
  "workspace": {
    "team_id": "T123ABC456",
    "team_name": "Acme Corp Slack",
    "company_name": "Acme Corp",
    "company_size": "11-50",
    "subscription_status": "trial",
    "subscription_tier": "growth"
  },
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "next_step": {
    "action": "channel_selection",
    "url": "/onboarding/channels?team_id=T123ABC456"
  }
}
```

---

## Workspace Management

### Get Workspace Channels
**`GET /api/v1/workspaces/{team_id}/channels`**

Get list of all channels in workspace.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Query Parameters:**
- `include_archived` (optional) - Include archived channels (default: false)
- `types` (optional) - Channel types: "public,private" (default)

**Response (200 OK):**
```json
{
  "team_id": "T123ABC456",
  "team_name": "Acme Corp",
  "channels": [
    {
      "channel_id": "C01ABC123",
      "name": "engineering",
      "is_private": false,
      "is_archived": false,
      "member_count": 42,
      "topic": "Engineering discussions",
      "purpose": "Talk about code",
      "is_member": true,
      "indexing_enabled": true,
      "indexing_status": "pending"
    }
  ],
  "total_channels": 10,
  "public_channels": 8,
  "private_channels": 2
}
```

---

### Configure Channel Indexing
**`POST /api/v1/workspaces/{team_id}/channels/configure`**

Select which channels to index and start indexing.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Request:**
```json
{
  "channel_ids": ["C01ABC123", "C02DEF456"],
  "start_indexing": true
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Channel configuration saved successfully",
  "team_id": "T123ABC456",
  "channels_configured": 2,
  "channels_enabled": ["C01ABC123", "C02DEF456"],
  "indexing_status": "in_progress",
  "next_step": {
    "action": "poll_status",
    "url": "/api/v1/workspaces/T123ABC456/status"
  }
}
```

---

### Get Workspace Status
**`GET /api/v1/workspaces/{team_id}/status`**

Get workspace status including indexing progress. **Poll every 5 seconds during indexing.**

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "team_id": "T123ABC456",
  "team_name": "Acme Corp",
  "subscription": {
    "status": "trial",
    "tier": "growth",
    "trial_days_remaining": 14,
    "trial_ends_at": "2025-10-26T00:00:00Z"
  },
  "indexing": {
    "status": "in_progress",
    "started_at": "2025-10-12T10:30:00Z",
    "completed_at": null,
    "progress_percentage": 45,
    "channels_indexed": 1,
    "channels_total": 2,
    "messages_indexed": 1250
  },
  "usage": {
    "queries_used_this_month": 0,
    "queries_limit": 2000,
    "queries_remaining": 2000
  },
  "is_ready": false
}
```

**Indexing Status Values:**
- `pending` - Not started
- `in_progress` - Currently indexing
- `complete` - Finished, bot ready
- `failed` - Error occurred

---

### Delete Workspace & All Data
**`DELETE /api/v1/workspaces/{team_id}`**

**⚠️ DANGER: This action is IRREVERSIBLE!**

Permanently delete workspace and ALL associated data. This endpoint will:
- ✅ Delete all database records (messages, threads, files, channels, users, etc.)
- ✅ Delete unified knowledge graph (all entities, relationships, cross-source nodes/edges)
- ✅ Delete all vector embeddings from Pinecone
- ✅ Delete all integration data (GitHub, Jira, etc.)
- ✅ Optionally uninstall the app from Slack workspace

**Authorization:**
- Requires valid Bearer token
- **Only workspace administrators** can delete the workspace
- User must belong to the workspace being deleted

**Confirmation Required:**
- Must provide exact workspace name as confirmation text
- Prevents accidental deletions

**Headers:**
```
Authorization: Bearer <access_token>
```

**Request Body:**
```json
{
  "confirmation_text": "Acme Corp Slack",
  "delete_from_slack": true
}
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `confirmation_text` | string | Yes | Must **exactly match** the workspace name (e.g., "Acme Corp Slack") |
| `delete_from_slack` | boolean | No | Whether to uninstall app from Slack workspace (default: true) |

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Workspace 'Acme Corp Slack' has been permanently deleted",
  "team_id": "T123ABC456",
  "workspace_name": "Acme Corp Slack",
  "deletion_stats": {
    "cross_source_edges": 145,
    "cross_source_nodes": 89,
    "entity_relationships": 69,
    "entities": 27,
    "messages": 25000,
    "threads": 1200,
    "files": 450,
    "channels": 12,
    "users": 50,
    "conversations": 350,
    "query_logs": 1250,
    "api_keys": 3,
    "integrations": 2,
    "external_content": 500
  }
}
```

**Response Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether deletion completed successfully |
| `message` | string | Human-readable confirmation message |
| `team_id` | string | Team ID that was deleted |
| `workspace_name` | string | Workspace name that was deleted |
| `deletion_stats` | object | Count of records deleted from each table |

**Error Responses:**

**400 Bad Request - Incorrect Confirmation:**
```json
{
  "detail": "Confirmation text must exactly match workspace name: 'Acme Corp Slack'"
}
```

**403 Forbidden - Not Admin:**
```json
{
  "detail": "Only workspace administrators can delete the workspace"
}
```

**403 Forbidden - Wrong Workspace:**
```json
{
  "detail": "You don't have access to this workspace"
}
```

**404 Not Found - Workspace Doesn't Exist:**
```json
{
  "detail": "Workspace not found"
}
```

**500 Internal Server Error:**
```json
{
  "detail": "Failed to delete workspace: <error details>"
}
```

---

### What Gets Deleted

When you delete a workspace, **everything** is permanently removed:

#### **1. Unified Knowledge Graph** 🔥
- All entity nodes (people, topics, technologies, projects, etc.)
- All relationships (co-occurs-with, related-to, works-with, etc.)
- All cross-source nodes (Slack messages, GitHub code, Jira issues)
- All cross-source edges (message → PR links, issue → code links)
- **Example entities deleted:** `entity:technical:postgresql`, `entity:concept:authentication`
- **Example relationships deleted:** PostgreSQL ↔ Redis co-occurrence links

#### **2. Slack Data**
- All messages from all channels
- All threads and replies
- All uploaded files
- All channel information
- All user profiles
- All conversation histories
- All query logs (search history)

#### **3. Integration Data**
- GitHub repositories and code files
- GitHub sync jobs and indexing status
- User integration connections (GitHub OAuth tokens)
- External content from all sources
- Integration sync jobs and logs

#### **4. Vector Database (Pinecone)**
- All semantic embeddings (message vectors)
- All code embeddings (GitHub code vectors)
- Namespaces: `{team_id}:semantic` and `{team_id}:code`

#### **5. Workspace & User Data**
- API keys
- User accounts
- Workspace settings
- Subscription information
- Workspace record itself

#### **6. Slack App (Optional)**
If `delete_from_slack: true`:
- Uninstalls the app from Slack workspace
- Removes bot from all channels
- Revokes OAuth tokens
- User won't see the bot anymore

---

### Deletion Order

The deletion happens in a specific order to maintain referential integrity:

```
1. External Integrations (GitHub, Jira connections)
2. Unified Knowledge Graph (cross_source_edges, cross_source_nodes)
3. Legacy Entity System (entity_relationships, entities)
4. Slack Data (messages, threads, files, channels)
5. Users (after all their data is deleted)
6. Workspace record (deleted last)
7. Vector Database (Pinecone namespaces)
8. Slack App Uninstall (if requested)
```

---

### UI Implementation Example

```typescript
// pages/settings/danger-zone.tsx
import { useState } from 'react';

interface DeleteWorkspaceModalProps {
  workspaceName: string;
  teamId: string;
  onSuccess: () => void;
  onClose: () => void;
}

export function DeleteWorkspaceModal({
  workspaceName,
  teamId,
  onSuccess,
  onClose
}: DeleteWorkspaceModalProps) {
  const [confirmationText, setConfirmationText] = useState('');
  const [deleteFromSlack, setDeleteFromSlack] = useState(true);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState('');

  async function handleDelete() {
    // Validate confirmation text
    if (confirmationText !== workspaceName) {
      setError(`Please type "${workspaceName}" exactly to confirm`);
      return;
    }

    setIsDeleting(true);
    setError('');

    try {
      const token = localStorage.getItem('access_token');

      const response = await fetch(`/api/v1/workspaces/${teamId}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          confirmation_text: confirmationText,
          delete_from_slack: deleteFromSlack
        })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to delete workspace');
      }

      const data = await response.json();

      // Show success message
      alert(`Workspace deleted successfully!\n\n${data.deletion_stats.messages} messages, ${data.deletion_stats.users} users, and all other data permanently removed.`);

      // Clear local auth
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');

      // Redirect to goodbye page or signup
      window.location.href = '/goodbye';

      onSuccess();

    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <div className="modal danger-modal">
      <div className="modal-content">
        <h2>⚠️ Delete Workspace</h2>
        <p className="warning-text">
          This action <strong>CANNOT be undone</strong>. This will permanently delete:
        </p>

        <ul className="deletion-list">
          <li>✅ All Slack messages and conversations</li>
          <li>✅ All knowledge graph entities and relationships</li>
          <li>✅ All GitHub integration data</li>
          <li>✅ All vector embeddings</li>
          <li>✅ All user accounts</li>
          <li>✅ Everything related to this workspace</li>
        </ul>

        {deleteFromSlack && (
          <p className="slack-uninstall-notice">
            🔴 This will also <strong>uninstall the app from Slack</strong>
          </p>
        )}

        <div className="form-group">
          <label>
            <input
              type="checkbox"
              checked={deleteFromSlack}
              onChange={(e) => setDeleteFromSlack(e.target.checked)}
            />
            Also uninstall app from Slack workspace
          </label>
        </div>

        <div className="form-group">
          <label>
            Type workspace name to confirm: <code>{workspaceName}</code>
          </label>
          <input
            type="text"
            value={confirmationText}
            onChange={(e) => setConfirmationText(e.target.value)}
            placeholder={workspaceName}
            className="confirmation-input"
            autoComplete="off"
          />
        </div>

        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        <div className="modal-actions">
          <button
            onClick={handleDelete}
            disabled={isDeleting || confirmationText !== workspaceName}
            className="btn-danger"
          >
            {isDeleting ? 'Deleting...' : 'Delete Workspace Forever'}
          </button>
          <button
            onClick={onClose}
            disabled={isDeleting}
            className="btn-secondary"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// Usage in Settings Page
export default function DangerZoneSettings() {
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const workspace = useWorkspace(); // Get from context

  return (
    <div className="settings-section danger-zone">
      <h2>🚨 Danger Zone</h2>

      <div className="danger-card">
        <div>
          <h3>Delete Workspace</h3>
          <p>Permanently delete this workspace and all its data</p>
        </div>
        <button
          onClick={() => setShowDeleteModal(true)}
          className="btn-danger"
        >
          Delete Workspace
        </button>
      </div>

      {showDeleteModal && (
        <DeleteWorkspaceModal
          workspaceName={workspace.team_name}
          teamId={workspace.team_id}
          onSuccess={() => {
            // Redirect to goodbye page
          }}
          onClose={() => setShowDeleteModal(false)}
        />
      )}
    </div>
  );
}
```

---

### Testing

```bash
# Get workspace name first
curl -X GET "https://api.eunoia.ai/api/v1/admin/dashboard/overview/T123ABC456" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  | jq '.workspace.team_name'

# Output: "Acme Corp Slack"

# Delete workspace (use exact name from above)
curl -X DELETE "https://api.eunoia.ai/api/v1/workspaces/T123ABC456" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "confirmation_text": "Acme Corp Slack",
    "delete_from_slack": true
  }'
```

---

### After Deletion

**What happens to users:**
1. Access tokens become invalid immediately
2. Refresh tokens stop working
3. Users can sign up again with same email (fresh start)
4. If `delete_from_slack: true`, bot disappears from Slack

**Data recovery:**
- ⚠️ **NO recovery possible** - deletion is permanent
- No backups are kept
- Knowledge graph cannot be restored
- If you reconnect, everything rebuilds from scratch (limited by Slack message history)

**Reconnecting after deletion:**
1. Admin can reinstall the app from Slack App Directory
2. Goes through onboarding flow again
3. Fetches recent message history (limited by Slack's retention)
4. Rebuilds knowledge graph from available data
5. **Loss:** Messages beyond Slack's history limit are gone forever
6. **Loss:** All computed analytics and insights are gone

---

## GitHub Integration

### Overview

The GitHub integration allows users to connect their GitHub accounts and index code repositories for enhanced search capabilities. When integrated, users can search both Slack messages AND GitHub code in unified search results.

**Key Features:**
- **Permission-Aware**: Users only see code from repositories they have access to
- **Dual Embedding**: Code structure (Voyage AI) + Semantic meaning (OpenAI)
- **Hybrid Search**: Combines code pattern matching with semantic understanding
- **Background Processing**: Async repository indexing with real-time progress
- **Unified Search**: GitHub code appears alongside Slack messages in search results

**Integration Points:**
1. **During Onboarding** - Optional step after channel selection
2. **Settings Page** - Connect/disconnect GitHub anytime

---

### GitHub OAuth Flow

#### Step 1: Initiate GitHub OAuth
**`GET /oauth/github/authorize`**

Start GitHub OAuth flow. This generates a GitHub authorization URL with CSRF protection.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "authorization_url": "https://github.com/login/oauth/authorize?client_id=...&redirect_uri=...&scope=repo+read:user&state=abc123...",
  "state": "abc123..."
}
```

**UI Implementation:**
```typescript
// User clicks "Connect GitHub" button
async function connectGitHub() {
  const response = await fetch('/oauth/github/authorize', {
    headers: { 'Authorization': `Bearer ${accessToken}` }
  });

  const data = await response.json();

  // Save state for verification after callback
  sessionStorage.setItem('github_oauth_state', data.state);

  // Redirect user to GitHub
  window.location.href = data.authorization_url;
}
```

---

#### Step 2: OAuth Callback (Automatic)
**`GET /oauth/github/callback`**

GitHub redirects here after user authorizes. Backend handles this automatically.

**Query Parameters:**
- `code` - Authorization code from GitHub
- `state` - CSRF protection token (must match from Step 1)

**Backend Processing:**
1. Validates `state` parameter
2. Exchanges `code` for GitHub access token
3. Fetches GitHub user info
4. Encrypts and stores access token (AES-256-GCM)
5. Creates `UserIntegrationConnection` record
6. Redirects to success URL

**Redirect URLs:**
- **Success**: `/integrations/github/success?status=connected`
- **Error**: `/integrations/github/error?error=<error_message>`

**UI Implementation:**
```typescript
// Page: /integrations/github/success
useEffect(() => {
  const params = new URLSearchParams(window.location.search);
  const status = params.get('status');

  if (status === 'connected') {
    // Show success message
    toast.success('GitHub connected successfully!');

    // Refresh GitHub connection status
    fetchGitHubStatus();

    // Redirect to repository selection (during onboarding)
    // OR stay on settings page (from settings)
    if (isOnboarding) {
      navigate('/onboarding/github-repos');
    } else {
      navigate('/settings/integrations');
    }
  }
}, []);
```

---

#### Step 3: Check Connection Status
**`GET /oauth/github/status`**

Check if user has active GitHub connection.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK) - Connected:**
```json
{
  "connected": true,
  "github_user": {
    "login": "johndoe",
    "id": 12345678,
    "name": "John Doe",
    "email": "john@example.com",
    "avatar_url": "https://avatars.githubusercontent.com/u/12345678",
    "company": "Acme Corp",
    "location": "San Francisco"
  },
  "scopes": ["repo", "read:user"],
  "connected_at": "2025-10-20T12:00:00Z",
  "repositories_indexed": 3,
  "total_files_indexed": 450
}
```

**Response (200 OK) - Not Connected:**
```json
{
  "connected": false,
  "github_user": null,
  "scopes": [],
  "connected_at": null,
  "repositories_indexed": 0,
  "total_files_indexed": 0
}
```

**UI Implementation:**
```typescript
interface GitHubStatus {
  connected: boolean;
  github_user?: {
    login: string;
    avatar_url: string;
    name: string;
  };
  repositories_indexed: number;
  total_files_indexed: number;
}

function GitHubIntegration() {
  const [status, setStatus] = useState<GitHubStatus | null>(null);

  useEffect(() => {
    fetchGitHubStatus();
  }, []);

  async function fetchGitHubStatus() {
    const response = await fetch('/oauth/github/status', {
      headers: { 'Authorization': `Bearer ${accessToken}` }
    });
    const data = await response.json();
    setStatus(data);
  }

  return (
    <div className="github-integration">
      {status?.connected ? (
        <div className="connected-state">
          <img src={status.github_user?.avatar_url} alt="GitHub avatar" />
          <h3>Connected as @{status.github_user?.login}</h3>
          <p>{status.repositories_indexed} repositories indexed</p>
          <p>{status.total_files_indexed} code files searchable</p>
          <button onClick={disconnectGitHub}>Disconnect</button>
        </div>
      ) : (
        <div className="not-connected-state">
          <h3>Connect GitHub</h3>
          <p>Search your code repositories directly in Slack</p>
          <button onClick={connectGitHub}>Connect GitHub Account</button>
        </div>
      )}
    </div>
  );
}
```

---

#### Step 4: Disconnect GitHub
**`POST /oauth/github/disconnect`**

Disconnect GitHub integration and remove access.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "GitHub disconnected successfully"
}
```

**UI Implementation:**
```typescript
async function disconnectGitHub() {
  const confirmed = confirm('Disconnect GitHub? Your indexed repositories will be removed.');
  if (!confirmed) return;

  await fetch('/oauth/github/disconnect', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${accessToken}` }
  });

  toast.success('GitHub disconnected');
  fetchGitHubStatus(); // Refresh status
}
```

---

### Repository Management

#### List Accessible Repositories
**`GET /api/v1/github/repositories`**

Get all repositories accessible to the connected GitHub user.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "repositories": [
    {
      "full_name": "acmecorp/backend",
      "description": "Backend API for Acme Corp",
      "language": "Python",
      "visibility": "private",
      "stars": 42,
      "forks": 7,
      "updated_at": "2025-10-20T12:00:00Z"
    },
    {
      "full_name": "johndoe/personal-project",
      "description": "My side project",
      "language": "TypeScript",
      "visibility": "public",
      "stars": 5,
      "forks": 1,
      "updated_at": "2025-10-19T10:00:00Z"
    }
  ],
  "total": 15
}
```

**Error (404) - No GitHub Connection:**
```json
{
  "detail": "No active GitHub connection. Please connect your GitHub account first."
}
```

---

#### List Indexed Repositories
**`GET /api/v1/github/indexed-repositories`**

Get list of repositories that have been indexed for search.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "repositories": [
    {
      "full_name": "acmecorp/backend",
      "repository_id": "R_kgDOH1234",
      "visibility": "private",
      "file_count": 127,
      "last_indexed_at": "2025-10-20T12:00:00Z"
    },
    {
      "full_name": "acmecorp/frontend",
      "repository_id": "R_kgDOH5678",
      "visibility": "private",
      "file_count": 89,
      "last_indexed_at": "2025-10-20T11:00:00Z"
    }
  ],
  "total": 2
}
```

---

#### Index a Repository
**`POST /api/v1/github/index-repository`**

Start indexing a repository. This creates a background job that:
1. Fetches all code files from GitHub
2. Stores them in database with permission metadata
3. Generates dual embeddings (code + semantic)
4. Stores embeddings in Pinecone for search

**Headers:**
```
Authorization: Bearer <access_token>
```

**Request:**
```json
{
  "owner": "acmecorp",
  "repo": "backend",
  "generate_embeddings": true
}
```

**Request Fields:**
- `owner` - Repository owner (user or organization)
- `repo` - Repository name
- `generate_embeddings` - Whether to generate embeddings immediately (default: true)

**Response (200 OK):**
```json
{
  "sync_job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Started indexing acmecorp/backend"
}
```

**UI Implementation:**
```typescript
async function indexRepository(owner: string, repo: string) {
  const response = await fetch('/api/v1/github/index-repository', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${accessToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      owner,
      repo,
      generate_embeddings: true
    })
  });

  const data = await response.json();

  // Start polling sync job status
  pollSyncJobStatus(data.sync_job_id);

  toast.success(`Started indexing ${owner}/${repo}`);
}
```

---

#### Monitor Sync Job Status
**`GET /api/v1/github/sync-jobs/{job_id}`**

Get detailed progress of repository indexing job. **Poll every 3-5 seconds during indexing.**

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK) - In Progress:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "integration_type": "github",
  "sync_type": "full",
  "target_resource": "repo:acmecorp/backend",
  "total_items": 500,
  "processed_items": 250,
  "failed_items": 2,
  "items_created": 230,
  "items_updated": 18,
  "started_at": "2025-10-20T12:00:00Z",
  "completed_at": null,
  "error_message": null
}
```

**Response (200 OK) - Completed:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "integration_type": "github",
  "sync_type": "full",
  "target_resource": "repo:acmecorp/backend",
  "total_items": 500,
  "processed_items": 500,
  "failed_items": 2,
  "items_created": 450,
  "items_updated": 48,
  "started_at": "2025-10-20T12:00:00Z",
  "completed_at": "2025-10-20T12:15:00Z",
  "error_message": null
}
```

**Sync Job Status Values:**
- `pending` - Not started yet
- `running` - Currently indexing
- `completed` - Finished successfully
- `failed` - Error occurred (see `error_message`)

**UI Implementation:**
```typescript
async function pollSyncJobStatus(jobId: string) {
  const pollInterval = setInterval(async () => {
    const response = await fetch(`/api/v1/github/sync-jobs/${jobId}`, {
      headers: { 'Authorization': `Bearer ${accessToken}` }
    });

    const job = await response.json();

    // Update progress UI
    const progress = (job.processed_items / job.total_items) * 100;
    setProgress(progress);

    // Check if complete
    if (job.status === 'completed') {
      clearInterval(pollInterval);
      toast.success('Repository indexed successfully!');
      refreshRepositoryList();
    } else if (job.status === 'failed') {
      clearInterval(pollInterval);
      toast.error(`Indexing failed: ${job.error_message}`);
    }
  }, 3000); // Poll every 3 seconds
}
```

---

#### List All Sync Jobs
**`GET /api/v1/github/sync-jobs`**

Get all sync jobs for the workspace.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Query Parameters:**
- `status` (optional) - Filter by status: `pending`, `running`, `completed`, `failed`
- `limit` (optional) - Max results (default: 50)

**Response (200 OK):**
```json
{
  "jobs": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "completed",
      "target_resource": "repo:acmecorp/backend",
      "total_items": 500,
      "processed_items": 500,
      "items_created": 450,
      "started_at": "2025-10-20T12:00:00Z",
      "completed_at": "2025-10-20T12:15:00Z"
    }
  ],
  "total": 5
}
```

---

### GitHub During Onboarding

**Flow: New User Signup**

```
1. Slack OAuth → Complete Profile → Channel Selection → [GitHub Integration] → Dashboard
```

**Implementation:**

After user completes channel selection and starts indexing, show GitHub integration as **optional step**:

```typescript
// pages/onboarding/github.tsx
export default function GitHubOnboarding() {
  const [githubStatus, setGithubStatus] = useState(null);
  const [repositories, setRepositories] = useState([]);
  const [selectedRepos, setSelectedRepos] = useState<string[]>([]);
  const [indexingJobs, setIndexingJobs] = useState<Map<string, any>>(new Map());

  useEffect(() => {
    checkGitHubStatus();
  }, []);

  async function checkGitHubStatus() {
    const status = await fetch('/oauth/github/status', {
      headers: { 'Authorization': `Bearer ${accessToken}` }
    }).then(r => r.json());

    setGithubStatus(status);

    if (status.connected) {
      // Fetch repositories
      loadRepositories();
    }
  }

  async function loadRepositories() {
    const repos = await fetch('/api/v1/github/repositories', {
      headers: { 'Authorization': `Bearer ${accessToken}` }
    }).then(r => r.json());

    setRepositories(repos.repositories);
  }

  async function startIndexing() {
    // Index each selected repository
    for (const repoFullName of selectedRepos) {
      const [owner, repo] = repoFullName.split('/');

      const response = await fetch('/api/v1/github/index-repository', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ owner, repo, generate_embeddings: true })
      });

      const data = await response.json();

      // Track job
      indexingJobs.set(repoFullName, { jobId: data.sync_job_id, status: 'pending' });
    }

    // Start polling
    pollAllJobs();
  }

  function pollAllJobs() {
    const interval = setInterval(async () => {
      let allComplete = true;

      for (const [repoName, jobInfo] of indexingJobs.entries()) {
        const job = await fetch(`/api/v1/github/sync-jobs/${jobInfo.jobId}`, {
          headers: { 'Authorization': `Bearer ${accessToken}` }
        }).then(r => r.json());

        indexingJobs.set(repoName, { ...jobInfo, ...job });

        if (job.status !== 'completed' && job.status !== 'failed') {
          allComplete = false;
        }
      }

      setIndexingJobs(new Map(indexingJobs)); // Trigger re-render

      if (allComplete) {
        clearInterval(interval);
        // Ready to proceed to dashboard
      }
    }, 3000);
  }

  if (!githubStatus?.connected) {
    return (
      <div className="github-onboarding">
        <h2>Connect GitHub (Optional)</h2>
        <p>Search your code repositories directly in Slack</p>

        <div className="benefits">
          <h3>Why connect GitHub?</h3>
          <ul>
            <li>Search code across repositories</li>
            <li>Find functions and implementations</li>
            <li>Get answers from your codebase</li>
          </ul>
        </div>

        <div className="actions">
          <button onClick={connectGitHub} className="primary">
            Connect GitHub
          </button>
          <button onClick={() => navigate('/dashboard')} className="secondary">
            Skip for now
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="github-repo-selection">
      <h2>Select Repositories to Index</h2>
      <p>Choose which repositories to make searchable in Slack</p>

      <div className="repository-list">
        {repositories.map(repo => (
          <label key={repo.full_name} className="repo-card">
            <input
              type="checkbox"
              checked={selectedRepos.includes(repo.full_name)}
              onChange={(e) => {
                if (e.target.checked) {
                  setSelectedRepos([...selectedRepos, repo.full_name]);
                } else {
                  setSelectedRepos(selectedRepos.filter(r => r !== repo.full_name));
                }
              }}
            />
            <div className="repo-info">
              <h4>{repo.full_name}</h4>
              <p>{repo.description}</p>
              <span className="language">{repo.language}</span>
              <span className="visibility">{repo.visibility}</span>
            </div>
          </label>
        ))}
      </div>

      {indexingJobs.size > 0 && (
        <div className="indexing-progress">
          <h3>Indexing Progress</h3>
          {Array.from(indexingJobs.entries()).map(([repo, job]) => (
            <div key={repo} className="job-progress">
              <span>{repo}</span>
              <progress value={job.processed_items} max={job.total_items} />
              <span>{job.status}</span>
            </div>
          ))}
        </div>
      )}

      <div className="actions">
        <button
          onClick={startIndexing}
          disabled={selectedRepos.length === 0 || indexingJobs.size > 0}
          className="primary"
        >
          Index Selected Repositories ({selectedRepos.length})
        </button>
        <button onClick={() => navigate('/dashboard')} className="secondary">
          {indexingJobs.size > 0 ? 'Continue to Dashboard' : 'Skip for now'}
        </button>
      </div>
    </div>
  );
}
```

---

### GitHub in Settings

**Flow: Existing User Connecting GitHub**

```
Settings → Integrations → Connect GitHub → Select Repos → Index → Done
```

**Implementation:**

```typescript
// pages/settings/integrations.tsx
export default function IntegrationsSettings() {
  const [githubStatus, setGithubStatus] = useState(null);
  const [showRepoModal, setShowRepoModal] = useState(false);

  useEffect(() => {
    fetchGitHubStatus();
  }, []);

  async function fetchGitHubStatus() {
    const status = await fetch('/oauth/github/status', {
      headers: { 'Authorization': `Bearer ${accessToken}` }
    }).then(r => r.json());

    setGithubStatus(status);
  }

  return (
    <div className="settings-page">
      <h1>Integrations</h1>

      <div className="integration-card">
        <div className="integration-header">
          <img src="/github-icon.svg" alt="GitHub" />
          <div>
            <h3>GitHub</h3>
            <p>Search your code repositories in Slack</p>
          </div>
        </div>

        {githubStatus?.connected ? (
          <div className="connected-state">
            <div className="user-info">
              <img src={githubStatus.github_user?.avatar_url} alt="Avatar" />
              <div>
                <strong>@{githubStatus.github_user?.login}</strong>
                <p>{githubStatus.repositories_indexed} repos indexed</p>
              </div>
            </div>

            <div className="actions">
              <button onClick={() => setShowRepoModal(true)}>
                Manage Repositories
              </button>
              <button onClick={disconnectGitHub} className="danger">
                Disconnect
              </button>
            </div>
          </div>
        ) : (
          <div className="not-connected-state">
            <p>Connect GitHub to search your code repositories</p>
            <button onClick={connectGitHub} className="primary">
              Connect GitHub
            </button>
          </div>
        )}
      </div>

      {showRepoModal && (
        <GitHubRepositoryModal
          onClose={() => setShowRepoModal(false)}
          onUpdate={fetchGitHubStatus}
        />
      )}
    </div>
  );
}
```

---

### How Search Works with GitHub

Once repositories are indexed, GitHub code automatically appears in search results when users ask questions in Slack:

**Example:**
```
User in Slack: "How do we handle user authentication?"

Bot Response:
📧 Slack Messages (2 results)
- #engineering: "We use JWT tokens for auth..." (John, 2 days ago)
- #backend: "Auth flow diagram attached" (Jane, 1 week ago)

💻 Code from GitHub (3 results)
- acmecorp/backend/auth/jwt.py: JWT token generation
- acmecorp/backend/middleware/auth.py: Auth middleware
- acmecorp/frontend/utils/auth.ts: Client-side auth helper
```

**Permission Filtering:**
- Users only see code from repositories they have GitHub access to
- Public repositories: Visible to all connected users
- Private repositories: Only visible to users with access

---

## Analytics Dashboards

### Home Dashboard Analytics
**`GET /api/v1/admin/dashboard/home/{team_id}`**

Get complete home dashboard analytics with all metrics and insights.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "queries_this_week": {
    "count": 150,
    "percentage_change": 25.5,
    "trend": "up",
    "comparison_period": "vs last week"
  },
  "accuracy_feedback": {
    "accuracy_percentage": 87.5,
    "percentage_change": 3.2,
    "trend": "up",
    "total_ratings": 45
  },
  "monitored_channels": {
    "count": 12
  },
  "queries_over_time": {
    "labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "this_week": [10, 15, 20, 25, 18, 12, 8],
    "last_week": [8, 12, 18, 22, 15, 10, 6]
  },
  "insights": {
    "top_searched_topics": [
      {
        "topic": "deployment",
        "count": 42
      },
      {
        "topic": "database",
        "count": 35
      }
    ],
    "most_active_user": {
      "user_id": "U123ABC456",
      "display_name": "John Doe",
      "query_count": 45,
      "accuracy_percentage": 87.5
    },
    "most_indexed_channel": {
      "channel_id": "C123ABC456",
      "channel_name": "engineering",
      "message_count": 15000
    }
  }
}
```

**Field Descriptions:**

**queries_this_week:**
- `count`: Total queries in last 7 days
- `percentage_change`: % change compared to previous 7 days
- `trend`: "up", "down", or "neutral"

**accuracy_feedback:**
- `accuracy_percentage`: (thumbs_up / total_ratings) * 100
- `percentage_change`: Change in accuracy % vs last week
- `total_ratings`: Number of rated queries this week
- Note: Rating 5 = thumbs up, Rating 1 = thumbs down

**monitored_channels:**
- `count`: Number of channels with indexing enabled and not archived

**queries_over_time:**
- `labels`: Days of the week
- `this_week`: Query counts for each day (last 7 days)
- `last_week`: Query counts for comparison (7-14 days ago)

**insights:**
- `top_searched_topics`: Top 5 keywords extracted from queries
- `most_active_user`: User with highest query count this week
- `most_indexed_channel`: Channel with most indexed messages

---

### Analytics & Usage Insights
**`GET /api/v1/admin/dashboard/analytics-insights/{team_id}`**

Get complete Analytics & Usage Insights page data with advanced metrics and user adoption statistics.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "total_queries": {
    "count": 150,
    "percentage_change": 25.5,
    "trend": "up",
    "comparison_period": "vs last week"
  },
  "active_users": {
    "count": 24,
    "percentage_change": 12.3,
    "trend": "up",
    "comparison_period": "vs last week"
  },
  "accuracy_feedback": {
    "accuracy_percentage": 87.5,
    "percentage_change": 3.2,
    "trend": "up",
    "total_ratings": 45
  },
  "time_saved": {
    "hours_saved": 12.5,
    "percentage_change": 25.5,
    "trend": "up",
    "comparison_period": "vs last week"
  },
  "queries_over_time": {
    "labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "data": [10, 15, 20, 25, 18, 12, 8]
  },
  "top_searched_topics": [
    {
      "topic": "deployment",
      "count": 42
    },
    {
      "topic": "database",
      "count": 35
    },
    {
      "topic": "authentication",
      "count": 28
    }
  ],
  "feedback_distribution": {
    "positive": 45,
    "negative": 10,
    "no_response": 95
  },
  "user_adoption": {
    "top_users": [
      {
        "user_id": "U123ABC456",
        "display_name": "John Doe",
        "query_count": 45,
        "accuracy_percentage": 91.2
      },
      {
        "user_id": "U789DEF012",
        "display_name": "Jane Smith",
        "query_count": 38,
        "accuracy_percentage": 85.7
      },
      {
        "user_id": "U345GHI678",
        "display_name": "Bob Johnson",
        "query_count": 32,
        "accuracy_percentage": 89.3
      },
      {
        "user_id": "U901JKL234",
        "display_name": "Alice Brown",
        "query_count": 28,
        "accuracy_percentage": 92.1
      },
      {
        "user_id": "U567MNO890",
        "display_name": "Charlie Davis",
        "query_count": 24,
        "accuracy_percentage": 87.5
      }
    ]
  }
}
```

**Field Descriptions:**

**total_queries:**
- Same as home dashboard `queries_this_week`
- Total queries answered in last 7 days with percentage change

**active_users:**
- `count`: Number of unique users who made queries this week
- `percentage_change`: Change compared to last week
- `trend`: "up", "down", or "neutral"

**accuracy_feedback:**
- Same as home dashboard
- Overall accuracy percentage with week-over-week change

**time_saved:**
- `hours_saved`: Estimated time saved this week (5 minutes per query assumption)
- `percentage_change`: Change compared to last week
- `trend`: "up", "down", or "neutral"

**queries_over_time:**
- `labels`: Days of the week (Mon through Sun)
- `data`: Query counts for current week only (not comparison)
- Use for single-week line/bar chart

**top_searched_topics:**
- Top 10 most frequent keywords from queries this week
- Simple keyword extraction with stop words filtering
- Can be displayed as histogram/bar chart

**feedback_distribution:**
- `positive`: Count of thumbs up (rating = 5)
- `negative`: Count of thumbs down (rating = 1)
- `no_response`: Count of queries with no rating
- Use for pie chart visualization

**user_adoption.top_users:**
- Top 5 most active users by query count
- `query_count`: Total queries asked this week
- `accuracy_percentage`: (positive_ratings / total_ratings) * 100
- `accuracy_percentage` can be `null` if user has no ratings
- Display as table or leaderboard

---

## Admin Dashboard

### Dashboard Overview
**`GET /api/v1/admin/dashboard/overview/{team_id}`**

Get comprehensive dashboard overview for workspace.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "workspace": {
    "team_id": "T123ABC456",
    "team_name": "Acme Corp",
    "team_domain": "acmecorp",
    "installed_at": "2025-10-01T10:00:00Z",
    "is_active": true,
    "user_count": 50,
    "active_user_count": 35
  },
  "subscription": {
    "status": "trial",
    "tier": "growth",
    "is_active": true,
    "trial_days_remaining": 14,
    "subscription_ends_at": null,
    "monthly_query_limit": 2000,
    "queries_used_this_month": 450,
    "queries_remaining": 1550,
    "usage_percentage": 22.5,
    "billing_status": "good"
  },
  "indexing": {
    "status": "complete",
    "channels_indexed": 12,
    "messages_indexed": 25000,
    "last_indexed": "2025-10-12T10:30:00Z"
  },
  "activity": {
    "last_activity": "2025-10-13T09:45:00Z",
    "total_queries_all_time": 1250,
    "queries_this_month": 450
  }
}
```

---

### Get Workspace Channels (Admin)
**`GET /api/v1/admin/dashboard/channels/{team_id}`**

Get all channels with detailed indexing status.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Query Parameters:**
- `include_archived` (optional) - Include archived channels (default: false)

**Response (200 OK):**
```json
{
  "team_id": "T123ABC456",
  "total_channels": 12,
  "channels": [
    {
      "channel_id": "C01ABC123",
      "channel_name": "engineering",
      "is_private": false,
      "is_archived": false,
      "member_count": 42,
      "is_indexed": true,
      "indexing_enabled": true,
      "indexing_status": "complete",
      "message_count": 15000,
      "last_indexed": "1696284000.123456",
      "created_at": "2025-01-15T10:00:00Z"
    }
  ]
}
```

---

## Frontend Implementation Examples

### Home Dashboard Page

```typescript
// pages/dashboard/home.tsx
import { useEffect, useState } from 'react';
import { Line } from 'react-chartjs-2';

interface HomeAnalytics {
  queries_this_week: {
    count: number;
    percentage_change: number;
    trend: string;
  };
  accuracy_feedback: {
    accuracy_percentage: number;
    percentage_change: number;
    trend: string;
    total_ratings: number;
  };
  monitored_channels: {
    count: number;
  };
  queries_over_time: {
    labels: string[];
    this_week: number[];
    last_week: number[];
  };
  insights: {
    top_searched_topics: Array<{topic: string; count: number}>;
    most_active_user: {
      display_name: string;
      query_count: number;
    } | null;
    most_indexed_channel: {
      channel_name: string;
      message_count: number;
    } | null;
  };
}

export default function HomeDashboard() {
  const [analytics, setAnalytics] = useState<HomeAnalytics | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchAnalytics();
  }, []);

  async function fetchAnalytics() {
    const teamId = 'T123ABC456'; // Get from auth context
    const token = localStorage.getItem('access_token');

    const response = await fetch(`/api/v1/admin/dashboard/home/${teamId}`, {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });

    const data = await response.json();
    setAnalytics(data);
    setLoading(false);
  }

  if (loading) return <div>Loading...</div>;
  if (!analytics) return <div>No data</div>;

  // Prepare chart data
  const chartData = {
    labels: analytics.queries_over_time.labels,
    datasets: [
      {
        label: 'This Week',
        data: analytics.queries_over_time.this_week,
        borderColor: 'rgb(75, 192, 192)',
        backgroundColor: 'rgba(75, 192, 192, 0.2)',
      },
      {
        label: 'Last Week',
        data: analytics.queries_over_time.last_week,
        borderColor: 'rgb(255, 99, 132)',
        backgroundColor: 'rgba(255, 99, 132, 0.2)',
      }
    ]
  };

  return (
    <div className="dashboard">
      <h1>Dashboard</h1>

      {/* Metric Cards */}
      <div className="metrics-grid">
        <MetricCard
          title="Queries Answered This Week"
          value={analytics.queries_this_week.count}
          change={analytics.queries_this_week.percentage_change}
          trend={analytics.queries_this_week.trend}
        />
        <MetricCard
          title="Accuracy Feedback"
          value={`${analytics.accuracy_feedback.accuracy_percentage}%`}
          change={analytics.accuracy_feedback.percentage_change}
          trend={analytics.accuracy_feedback.trend}
          subtitle={`${analytics.accuracy_feedback.total_ratings} ratings`}
        />
        <MetricCard
          title="Channels Monitored"
          value={analytics.monitored_channels.count}
        />
      </div>

      {/* Queries Over Time Graph */}
      <div className="chart-container">
        <h2>Queries Over Time</h2>
        <Line data={chartData} />
      </div>

      {/* Workspace Insights */}
      <div className="insights-section">
        <h2>Workspace Insights</h2>
        <div className="insights-grid">
          {/* Top Topics, Most Active User, Most Indexed Channel */}
        </div>
      </div>
    </div>
  );
}
```

---

### Analytics & Usage Insights Page

```typescript
// pages/dashboard/analytics.tsx
import { useEffect, useState } from 'react';
import { Line, Bar, Pie } from 'react-chartjs-2';

interface AnalyticsInsights {
  total_queries: {
    count: number;
    percentage_change: number;
    trend: string;
  };
  active_users: {
    count: number;
    percentage_change: number;
    trend: string;
  };
  accuracy_feedback: {
    accuracy_percentage: number;
    percentage_change: number;
    trend: string;
    total_ratings: number;
  };
  time_saved: {
    hours_saved: number;
    percentage_change: number;
    trend: string;
  };
  queries_over_time: {
    labels: string[];
    data: number[];
  };
  top_searched_topics: Array<{topic: string; count: number}>;
  feedback_distribution: {
    positive: number;
    negative: number;
    no_response: number;
  };
  user_adoption: {
    top_users: Array<{
      user_id: string;
      display_name: string;
      query_count: number;
      accuracy_percentage: number | null;
    }>;
  };
}

export default function AnalyticsPage() {
  const [analytics, setAnalytics] = useState<AnalyticsInsights | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchAnalytics();
  }, []);

  async function fetchAnalytics() {
    const teamId = 'T123ABC456'; // Get from auth context
    const token = localStorage.getItem('access_token');

    const response = await fetch(`/api/v1/admin/dashboard/analytics-insights/${teamId}`, {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });

    const data = await response.json();
    setAnalytics(data);
    setLoading(false);
  }

  if (loading) return <div>Loading...</div>;
  if (!analytics) return <div>No data</div>;

  // Prepare Queries Over Time chart
  const queriesChartData = {
    labels: analytics.queries_over_time.labels,
    datasets: [{
      label: 'Queries This Week',
      data: analytics.queries_over_time.data,
      borderColor: 'rgb(75, 192, 192)',
      backgroundColor: 'rgba(75, 192, 192, 0.2)',
    }]
  };

  // Prepare Top Topics histogram
  const topicsChartData = {
    labels: analytics.top_searched_topics.map(t => t.topic),
    datasets: [{
      label: 'Search Count',
      data: analytics.top_searched_topics.map(t => t.count),
      backgroundColor: 'rgba(54, 162, 235, 0.6)',
    }]
  };

  // Prepare Feedback Distribution pie chart
  const feedbackChartData = {
    labels: ['Positive', 'Negative', 'No Response'],
    datasets: [{
      data: [
        analytics.feedback_distribution.positive,
        analytics.feedback_distribution.negative,
        analytics.feedback_distribution.no_response
      ],
      backgroundColor: [
        'rgba(75, 192, 192, 0.6)',
        'rgba(255, 99, 132, 0.6)',
        'rgba(201, 203, 207, 0.6)'
      ],
    }]
  };

  return (
    <div className="analytics-page">
      <h1>Analytics & Usage Insights</h1>

      {/* Top Metric Cards */}
      <div className="metrics-grid">
        <MetricCard
          title="Total Queries"
          value={analytics.total_queries.count}
          change={analytics.total_queries.percentage_change}
          trend={analytics.total_queries.trend}
        />
        <MetricCard
          title="Active Users"
          value={analytics.active_users.count}
          change={analytics.active_users.percentage_change}
          trend={analytics.active_users.trend}
        />
        <MetricCard
          title="Accuracy Feedback"
          value={`${analytics.accuracy_feedback.accuracy_percentage}%`}
          change={analytics.accuracy_feedback.percentage_change}
          trend={analytics.accuracy_feedback.trend}
        />
        <MetricCard
          title="Time Saved This Week"
          value={`${analytics.time_saved.hours_saved}h`}
          change={analytics.time_saved.percentage_change}
          trend={analytics.time_saved.trend}
        />
      </div>

      {/* Queries Over Time (Single Week) */}
      <div className="chart-container">
        <h2>Queries Over Time</h2>
        <Line data={queriesChartData} />
      </div>

      {/* Top Searched Topics Histogram */}
      <div className="chart-container">
        <h2>Top Searched Topics</h2>
        <Bar data={topicsChartData} options={{
          indexAxis: 'y',
          responsive: true
        }} />
      </div>

      {/* Feedback Distribution Pie Chart */}
      <div className="chart-container">
        <h2>Feedback Distribution</h2>
        <Pie data={feedbackChartData} />
      </div>

      {/* User Adoption Table */}
      <div className="user-adoption-section">
        <h2>User Adoption - Top 5 Most Active Users</h2>
        <table className="user-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Total Queries</th>
              <th>Avg Accuracy</th>
            </tr>
          </thead>
          <tbody>
            {analytics.user_adoption.top_users.map((user) => (
              <tr key={user.user_id}>
                <td>{user.display_name}</td>
                <td>{user.query_count}</td>
                <td>
                  {user.accuracy_percentage !== null
                    ? `${user.accuracy_percentage}%`
                    : 'N/A'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

---

### Metric Card Component

```typescript
// components/MetricCard.tsx
interface MetricCardProps {
  title: string;
  value: string | number;
  change?: number;
  trend?: 'up' | 'down' | 'neutral';
  subtitle?: string;
}

export function MetricCard({ title, value, change, trend, subtitle }: MetricCardProps) {
  return (
    <div className="metric-card">
      <h3>{title}</h3>
      <div className="metric-value">{value}</div>
      {change !== undefined && trend && (
        <div className={`metric-change ${trend}`}>
          {trend === 'up' ? '↑' : trend === 'down' ? '↓' : '→'}
          {Math.abs(change)}%
          <span> vs last week</span>
        </div>
      )}
      {subtitle && <div className="metric-subtitle">{subtitle}</div>}
    </div>
  );
}
```

---

## Error Handling

All endpoints return errors in this format:

```json
{
  "detail": "Error message here"
}
```

### Common HTTP Status Codes

| Code | Meaning | Action |
|------|---------|--------|
| `200` | Success | Process response |
| `400` | Bad Request | Show validation errors |
| `401` | Unauthorized | Refresh token or redirect to login |
| `403` | Forbidden | Show "No permission" message |
| `404` | Not Found | Show "Not found" message |
| `422` | Validation Error | Show field-specific errors |
| `500` | Server Error | Show generic error message |

---

## Testing Examples

### Test Home Analytics Endpoint

```bash
curl -X GET "http://localhost:8000/api/v1/admin/dashboard/home/T123ABC456" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Test Analytics Insights Endpoint

```bash
curl -X GET "http://localhost:8000/api/v1/admin/dashboard/analytics-insights/T123ABC456" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Test with Authentication

```bash
# 1. Login first
TOKEN=$(curl -X POST "http://localhost:8000/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"password123"}' \
  | jq -r '.access_token')

# 2. Use token to fetch analytics
curl -X GET "http://localhost:8000/api/v1/admin/dashboard/analytics-insights/T123ABC456" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Summary

### Key Endpoints for UI Implementation

| Endpoint | Method | Use Case |
|----------|--------|----------|
| `/api/v1/auth/login` | POST | Sign in returning users |
| `/api/v1/auth/refresh` | POST | Refresh expired access token |
| `/api/v1/auth/me` | GET | Check auth status |
| `/oauth/install` | GET | Start OAuth for new users |
| `/api/v1/onboarding/complete-profile` | POST | Set password after OAuth |
| `/api/v1/workspaces/{team_id}/channels` | GET | List channels |
| `/api/v1/workspaces/{team_id}/channels/configure` | POST | Start indexing |
| `/api/v1/workspaces/{team_id}/status` | GET | Poll indexing progress |
| `/api/v1/admin/dashboard/home/{team_id}` | GET | **Home dashboard analytics** |
| `/api/v1/admin/dashboard/analytics-insights/{team_id}` | GET | **Analytics & Usage Insights page** |
| `/api/v1/admin/dashboard/overview/{team_id}` | GET | Workspace overview |

### Analytics Metrics Available

#### Home Dashboard
1. **Queries This Week** - Count with week-over-week % change
2. **Accuracy Feedback** - Thumbs up/down percentage with trend
3. **Monitored Channels** - Count of indexed channels
4. **Queries Over Time** - 7-day comparison graph (this week vs last week)
5. **Top Searched Topics** - Most frequent keywords from queries
6. **Most Active User** - User with highest query count
7. **Most Indexed Channel** - Channel with most messages

#### Analytics & Usage Insights
1. **Total Queries** - Same as home dashboard
2. **Active Users** - Unique users count with % change
3. **Accuracy Feedback** - Same as home dashboard
4. **Time Saved** - Hours saved (5 min per query) with % change
5. **Queries Over Time** - Single week graph (not comparison)
6. **Top Searched Topics** - Top 10 keywords for histogram
7. **Feedback Distribution** - Pie chart (positive/negative/no response)
8. **User Adoption** - Top 5 users with query counts and accuracy

### Time Saved Calculation

The system assumes each query saves approximately **5 minutes** of manual search time. This is calculated as:

```
hours_saved = (query_count * 5 minutes) / 60
```

This assumption can be adjusted in the `analytics_service.py` file by modifying the `MINUTES_SAVED_PER_QUERY` constant.

### Feedback System

- **Thumbs Up** = Rating 5 (positive)
- **Thumbs Down** = Rating 1 (negative)
- **No Response** = NULL rating
- **Accuracy** = (positive / total_ratings) * 100

### Authentication Flow

1. **New Users**: OAuth → Profile → Channel Select → Indexing → Dashboard
2. **Returning Users**: Login → Dashboard
3. **Reinstallation**: OAuth (again) → Redirect to Login
4. **Token Expired**: Auto-refresh → Continue
