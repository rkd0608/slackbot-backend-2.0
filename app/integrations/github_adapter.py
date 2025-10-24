"""
GitHub adapter implementation

Implements BaseSourceAdapter for GitHub integration, wrapping existing GitHub services
into the unified integration framework.
"""
import re
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.logging import get_logger
from app.integrations.base_adapter import (
    BaseSourceAdapter,
    SourceCapabilities,
    UnifiedDocument,
    NodeType,
    EdgeType
)
from app.services.github_services.github_api_client import GitHubAPIClient
from app.models.integration import ExternalContent

logger = get_logger(__name__)


class GitHubAdapter(BaseSourceAdapter):
    """
    GitHub integration adapter

    Provides access to GitHub repositories, issues, pull requests, and code files
    through the unified BaseSourceAdapter interface.
    """

    def __init__(self, team_id: str, credentials: Dict[str, Any]):
        """
        Initialize GitHub adapter

        Args:
            team_id: Workspace team ID
            credentials: Must contain 'access_token' key with GitHub OAuth token
        """
        super().__init__(team_id, credentials)

        if 'access_token' not in credentials:
            raise ValueError("GitHub adapter requires 'access_token' in credentials")

        self.api_client = GitHubAPIClient(access_token=credentials['access_token'])

    def get_source_name(self) -> str:
        return "github"

    def get_capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            # Data access
            supports_search=True,
            supports_incremental_sync=True,
            supports_webhooks=True,

            # Content types
            supports_full_text=True,
            supports_code=True,
            supports_files=True,

            # Metadata
            supports_permissions=True,
            supports_versioning=True,
            supports_comments=True,

            # Relationships
            can_detect_links=True,
            link_patterns=[
                r'https?://github\.com/[\w-]+/[\w-]+/(?:issues|pull)/\d+',
                r'(?:fixes|closes|resolves)\s+#\d+',
                r'#\d+',
                r'GH-\d+',
            ]
        )

    async def authenticate(self) -> bool:
        """Verify GitHub API credentials"""
        try:
            user_data = await self.api_client.get_authenticated_user()
            logger.info(
                "github_authentication_successful",
                team_id=self.team_id,
                username=user_data.get('login')
            )
            return True
        except Exception as e:
            logger.error(
                "github_authentication_failed",
                team_id=self.team_id,
                error=str(e)
            )
            return False

    async def fetch_incremental(
        self,
        since: datetime,
        limit: Optional[int] = None
    ) -> List[UnifiedDocument]:
        """
        Fetch GitHub content updated since timestamp

        This retrieves from the existing ExternalContent table where GitHub data
        is already indexed. For new indexing, use the indexing service directly.

        Args:
            since: Only fetch content updated after this time
            limit: Maximum number of documents to return

        Returns:
            List of UnifiedDocument objects
        """
        try:
            from app.core.database import get_db

            # Note: This is a simplified implementation that reads from existing indexed data
            # For full sync, use GitHubIndexingService directly
            logger.info(
                "github_fetch_incremental_started",
                team_id=self.team_id,
                since=since.isoformat()
            )

            # In production, you'd query ExternalContent or fetch from GitHub API
            # For now, return empty list as the actual syncing happens via GitHubIndexingService
            logger.warning(
                "github_incremental_sync_not_implemented",
                message="Use GitHubIndexingService.sync_repository() for actual syncing"
            )

            return []

        except Exception as e:
            logger.error(
                "github_fetch_incremental_failed",
                team_id=self.team_id,
                error=str(e)
            )
            raise

    async def fetch_by_id(self, source_id: str) -> Optional[UnifiedDocument]:
        """
        Fetch a specific GitHub item by ID

        Args:
            source_id: Format depends on node type:
                - Repository: "owner/repo"
                - Issue: "owner/repo/issues/123"
                - PR: "owner/repo/pull/456"
                - File: "owner/repo/blob/main/path/to/file.py"

        Returns:
            UnifiedDocument or None if not found
        """
        try:
            # Parse source_id to determine type
            parts = source_id.split('/')

            if len(parts) == 2:
                # Repository
                owner, repo = parts
                repo_data = await self.api_client.get_repository(owner, repo)
                return self._convert_repo_to_document(repo_data)

            elif len(parts) >= 4 and parts[2] == 'issues':
                # Issue
                owner, repo = parts[0], parts[1]
                issue_number = int(parts[3])
                issue_data = await self.api_client.get_issue(owner, repo, issue_number)
                return self._convert_issue_to_document(issue_data, owner, repo)

            elif len(parts) >= 4 and parts[2] == 'pull':
                # Pull Request
                owner, repo = parts[0], parts[1]
                pr_number = int(parts[3])
                pr_data = await self.api_client.get_pull_request(owner, repo, pr_number)
                return self._convert_pr_to_document(pr_data, owner, repo)

            else:
                logger.warning("github_invalid_source_id", source_id=source_id)
                return None

        except Exception as e:
            logger.error(
                "github_fetch_by_id_failed",
                source_id=source_id,
                error=str(e)
            )
            return None

    async def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20
    ) -> List[UnifiedDocument]:
        """
        Search GitHub content

        Args:
            query: Search query string
            filters: Optional filters:
                - repo: Repository name (owner/repo)
                - type: Content type (issue, pr, code, commit)
                - language: Programming language
                - state: Issue/PR state (open, closed)
            limit: Maximum results

        Returns:
            List of matching UnifiedDocument objects
        """
        try:
            filters = filters or {}
            results = []

            search_type = filters.get('type', 'code')

            if search_type == 'code':
                # Search code
                code_results = await self.api_client.search_code(
                    query=query,
                    repo=filters.get('repo'),
                    language=filters.get('language'),
                    per_page=limit
                )
                for item in code_results.get('items', []):
                    results.append(self._convert_code_file_to_document(item))

            elif search_type == 'issue':
                # Search issues
                issue_results = await self.api_client.search_issues(
                    query=query,
                    repo=filters.get('repo'),
                    state=filters.get('state'),
                    per_page=limit
                )
                for item in issue_results.get('items', []):
                    if 'pull_request' in item:
                        results.append(self._convert_pr_to_document(
                            item,
                            item['repository_url'].split('/')[-2],
                            item['repository_url'].split('/')[-1]
                        ))
                    else:
                        results.append(self._convert_issue_to_document(
                            item,
                            item['repository_url'].split('/')[-2],
                            item['repository_url'].split('/')[-1]
                        ))

            logger.info(
                "github_search_completed",
                team_id=self.team_id,
                query=query,
                results=len(results)
            )

            return results

        except Exception as e:
            logger.error(
                "github_search_failed",
                team_id=self.team_id,
                query=query,
                error=str(e)
            )
            return []

    async def detect_outbound_links(
        self,
        document: UnifiedDocument
    ) -> List[Dict[str, Any]]:
        """
        Detect references to other sources in GitHub content

        Detects:
        - GitHub issue/PR references (#123, owner/repo#456)
        - Explicit URLs to other GitHub items
        - Jira ticket references (PROJ-123)
        - Confluence page references

        Args:
            document: Document to analyze

        Returns:
            List of detected links
        """
        links = []
        content = f"{document.title} {document.content}"

        # GitHub issue/PR references
        # Pattern: #123 or owner/repo#456
        issue_pattern = r'(?:(\w+/\w+)#|#)(\d+)'
        for match in re.finditer(issue_pattern, content):
            repo_ref = match.group(1)
            issue_num = match.group(2)

            # Determine if it's from same repo or different repo
            target_id = f"{repo_ref}/issues/{issue_num}" if repo_ref else f"issues/{issue_num}"

            links.append({
                'target_source': 'github',
                'target_type': 'issue',  # Could be issue or PR
                'target_id': target_id,
                'confidence': 0.9 if repo_ref else 0.7,  # Lower confidence for same-repo refs
                'evidence': 'Issue/PR reference pattern',
                'link_text': match.group(0)
            })

        # Explicit GitHub URLs
        url_pattern = r'https?://github\.com/([\w-]+)/([\w-]+)/(issues|pull)/(\d+)'
        for match in re.finditer(url_pattern, content):
            owner, repo, item_type, number = match.groups()

            links.append({
                'target_source': 'github',
                'target_type': 'pull_request' if item_type == 'pull' else 'issue',
                'target_id': f"{owner}/{repo}/{item_type}/{number}",
                'confidence': 1.0,
                'evidence': 'Full URL found in text',
                'link_text': match.group(0)
            })

        # Jira ticket references (future integration)
        jira_pattern = r'\b([A-Z]{2,10}-\d+)\b'
        for match in re.finditer(jira_pattern, content):
            ticket_id = match.group(1)

            links.append({
                'target_source': 'jira',
                'target_type': 'issue',
                'target_id': ticket_id,
                'confidence': 0.8,
                'evidence': 'Jira ticket pattern (KEY-123)',
                'link_text': ticket_id
            })

        logger.info(
            "github_links_detected",
            canonical_id=document.canonical_id,
            links_found=len(links)
        )

        return links

    # Helper methods for converting GitHub data to UnifiedDocument

    def _convert_repo_to_document(self, repo_data: Dict) -> UnifiedDocument:
        """Convert GitHub repository data to UnifiedDocument"""
        return UnifiedDocument(
            source="github",
            source_id=repo_data['full_name'],
            canonical_id=f"github:{repo_data['full_name']}",
            node_type=NodeType.REPOSITORY,
            title=repo_data['name'],
            content=repo_data.get('description', ''),
            summary=repo_data.get('description', ''),
            url=repo_data['html_url'],
            author=repo_data['owner']['login'],
            author_id=str(repo_data['owner']['id']),
            created_at=datetime.fromisoformat(repo_data['created_at'].replace('Z', '+00:00')),
            updated_at=datetime.fromisoformat(repo_data['updated_at'].replace('Z', '+00:00')),
            visibility='public' if not repo_data.get('private') else 'private',
            project_context={
                'language': repo_data.get('language'),
                'stars': repo_data.get('stargazers_count', 0),
                'forks': repo_data.get('forks_count', 0),
            },
            tags=[repo_data.get('language')] if repo_data.get('language') else [],
            raw_data=repo_data
        )

    def _convert_issue_to_document(self, issue_data: Dict, owner: str, repo: str) -> UnifiedDocument:
        """Convert GitHub issue data to UnifiedDocument"""
        return UnifiedDocument(
            source="github",
            source_id=f"{owner}/{repo}/issues/{issue_data['number']}",
            canonical_id=f"github:{owner}/{repo}/issues/{issue_data['number']}",
            node_type=NodeType.ISSUE,
            title=issue_data['title'],
            content=issue_data.get('body', ''),
            summary=issue_data['title'],
            url=issue_data['html_url'],
            author=issue_data['user']['login'],
            author_id=str(issue_data['user']['id']),
            created_at=datetime.fromisoformat(issue_data['created_at'].replace('Z', '+00:00')),
            updated_at=datetime.fromisoformat(issue_data['updated_at'].replace('Z', '+00:00')),
            visibility='public',
            project_context={'repository': f"{owner}/{repo}"},
            tags=[label['name'] for label in issue_data.get('labels', [])],
            raw_data=issue_data
        )

    def _convert_pr_to_document(self, pr_data: Dict, owner: str, repo: str) -> UnifiedDocument:
        """Convert GitHub pull request data to UnifiedDocument"""
        return UnifiedDocument(
            source="github",
            source_id=f"{owner}/{repo}/pull/{pr_data['number']}",
            canonical_id=f"github:{owner}/{repo}/pull/{pr_data['number']}",
            node_type=NodeType.PULL_REQUEST,
            title=pr_data['title'],
            content=pr_data.get('body', ''),
            summary=pr_data['title'],
            url=pr_data['html_url'],
            author=pr_data['user']['login'],
            author_id=str(pr_data['user']['id']),
            created_at=datetime.fromisoformat(pr_data['created_at'].replace('Z', '+00:00')),
            updated_at=datetime.fromisoformat(pr_data['updated_at'].replace('Z', '+00:00')),
            visibility='public',
            project_context={'repository': f"{owner}/{repo}"},
            tags=[label['name'] for label in pr_data.get('labels', [])],
            raw_data=pr_data
        )

    def _convert_code_file_to_document(self, file_data: Dict) -> UnifiedDocument:
        """Convert GitHub code file data to UnifiedDocument"""
        repo = file_data['repository']

        return UnifiedDocument(
            source="github",
            source_id=f"{repo['full_name']}/blob/{file_data.get('sha', 'main')}/{file_data['path']}",
            canonical_id=f"github:{repo['full_name']}/blob/{file_data.get('sha', 'main')}/{file_data['path']}",
            node_type=NodeType.CODE_FILE,
            title=file_data['name'],
            content=file_data.get('content', ''),  # May need to fetch separately
            summary=f"Code file: {file_data['path']}",
            url=file_data['html_url'],
            author=None,  # Would need to fetch from commit history
            author_id=None,
            created_at=datetime.utcnow(),  # Would need to fetch from git history
            updated_at=datetime.utcnow(),
            visibility='public' if not repo.get('private') else 'private',
            project_context={
                'repository': repo['full_name'],
                'path': file_data['path'],
            },
            tags=[],
            raw_data=file_data
        )

    def get_node_types(self):
        """Return GitHub-specific node types"""
        return {
            NodeType.REPOSITORY,
            NodeType.PULL_REQUEST,
            NodeType.ISSUE,
            NodeType.CODE_FILE,
            NodeType.COMMIT,
        }

    def get_edge_types(self):
        """Return GitHub-specific edge types"""
        return {
            EdgeType.REFERENCES,
            EdgeType.FIXES,
            EdgeType.MODIFIES,
            EdgeType.IMPLEMENTS,
            EdgeType.REVIEWED_BY,
            EdgeType.DISCUSSES,
            EdgeType.PART_OF,
        }
