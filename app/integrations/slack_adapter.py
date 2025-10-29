"""
Slack Adapter for Unified Integration Architecture

Implements BaseSourceAdapter for Slack messages and threads.
Enables link detection from Slack to GitHub, Jira, Confluence, etc.
"""
import re
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.integrations.base_adapter import (
    BaseSourceAdapter,
    SourceCapabilities,
    UnifiedDocument,
    NodeType,
    EdgeType
)
from app.core.logging import get_logger

logger = get_logger(__name__)


class SlackAdapter(BaseSourceAdapter):
    """
    Adapter for Slack integration

    Detects links from Slack messages to:
    - GitHub PRs, issues, commits
    - Jira tickets
    - Confluence pages
    - Other Slack messages/threads
    """

    def get_source_name(self) -> str:
        """Return source identifier"""
        return "slack"

    def get_capabilities(self) -> SourceCapabilities:
        """Return Slack adapter capabilities"""
        return SourceCapabilities(
            supports_search=True,
            supports_incremental_sync=True,
            supports_webhooks=True,
            supports_full_text=True,
            supports_code=True,  # Code snippets in messages
            supports_files=True,  # File attachments
            supports_permissions=False,  # Slack channels have permissions but we don't model them yet
            supports_versioning=False,  # Message edits tracked separately
            supports_comments=True,  # Thread replies
            can_detect_links=True,
            link_patterns=[
                # GitHub patterns
                r'https?://github\.com/[\w-]+/[\w-]+/pull/\d+',
                r'https?://github\.com/[\w-]+/[\w-]+/issues/\d+',
                r'https?://github\.com/[\w-]+/[\w-]+/commit/[a-f0-9]+',
                r'#\d+',  # Issue/PR shorthand

                # Jira patterns
                r'\b[A-Z]{2,10}-\d+\b',
                r'https?://[\w.-]+\.atlassian\.net/browse/[A-Z]+-\d+',

                # Confluence patterns
                r'https?://[\w.-]+\.atlassian\.net/wiki/spaces/\w+/pages/\d+',

                # Linear patterns
                r'\b[A-Z]{3,5}-\d+\b',

                # Slack message/thread links
                r'https?://[\w.-]+\.slack\.com/archives/C[\w]+/p\d+'
            ]
        )

    async def authenticate(self) -> bool:
        """
        Verify Slack authentication

        For Slack, we assume the bot token is already validated
        since this adapter is used for existing data migration
        """
        # In a real implementation, we'd verify the bot token
        # For now, assume it's valid
        return True

    async def fetch_incremental(
        self,
        since: datetime,
        limit: Optional[int] = None
    ) -> List[UnifiedDocument]:
        """
        Fetch Slack messages updated since timestamp

        Note: For the backfill use case, this isn't used.
        In production, this would call Slack API to fetch new messages.
        """
        # This would be implemented to call Slack API
        # For now, we rely on the existing message ingestion pipeline
        logger.warning("fetch_incremental not implemented for Slack adapter")
        return []

    async def fetch_by_id(self, source_id: str) -> Optional[UnifiedDocument]:
        """
        Fetch a specific Slack message by ID

        Args:
            source_id: Message timestamp (e.g., "1234567890.123456")
        """
        # Would query the messages table
        logger.warning("fetch_by_id not implemented for Slack adapter")
        return None

    async def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20
    ) -> List[UnifiedDocument]:
        """
        Search Slack messages

        Would use existing search infrastructure
        """
        logger.warning("search not implemented for Slack adapter")
        return []

    async def detect_outbound_links(
        self,
        document: UnifiedDocument
    ) -> List[Dict[str, Any]]:
        """
        Detect links from Slack message to other sources

        Analyzes message text for:
        - GitHub URLs and issue/PR numbers
        - Jira ticket references
        - Confluence page links
        - Other source references
        """
        links = []
        content = f"{document.title} {document.content or ''}"

        # GitHub PR links
        github_pr_pattern = r'https://github\.com/([\w-]+)/([\w-]+)/pull/(\d+)'
        for match in re.finditer(github_pr_pattern, content, re.IGNORECASE):
            owner, repo, pr_num = match.groups()
            links.append({
                'target_source': 'github',
                'target_type': 'pull_request',
                'target_id': f'{owner}/{repo}/pull/{pr_num}',
                'canonical_id': f'github:{owner}/{repo}/pull/{pr_num}',
                'confidence': 1.0,
                'evidence': 'GitHub PR URL found in message',
                'link_text': match.group(0),
                'edge_type': EdgeType.REFERENCES
            })

        # GitHub issue links
        github_issue_pattern = r'https://github\.com/([\w-]+)/([\w-]+)/issues/(\d+)'
        for match in re.finditer(github_issue_pattern, content, re.IGNORECASE):
            owner, repo, issue_num = match.groups()
            links.append({
                'target_source': 'github',
                'target_type': 'issue',
                'target_id': f'{owner}/{repo}/issues/{issue_num}',
                'canonical_id': f'github:{owner}/{repo}/issues/{issue_num}',
                'confidence': 1.0,
                'evidence': 'GitHub issue URL found in message',
                'link_text': match.group(0),
                'edge_type': EdgeType.DISCUSSES
            })

        # GitHub commit links
        github_commit_pattern = r'https://github\.com/([\w-]+)/([\w-]+)/commit/([a-f0-9]{7,40})'
        for match in re.finditer(github_commit_pattern, content, re.IGNORECASE):
            owner, repo, commit_sha = match.groups()
            links.append({
                'target_source': 'github',
                'target_type': 'commit',
                'target_id': f'{owner}/{repo}/commit/{commit_sha}',
                'canonical_id': f'github:{owner}/{repo}/commit/{commit_sha}',
                'confidence': 1.0,
                'evidence': 'GitHub commit URL found in message',
                'link_text': match.group(0),
                'edge_type': EdgeType.DISCUSSES
            })

        # GitHub shorthand (#123) - only if we have repo context
        # This is tricky because #123 could mean many things
        # We'd need additional context to resolve it
        shorthand_pattern = r'(?:^|\s)#(\d+)(?:\s|$)'
        for match in re.finditer(shorthand_pattern, content):
            issue_num = match.group(1)
            # Only add if we have high confidence (e.g., other GitHub links in message)
            if any(link['target_source'] == 'github' for link in links):
                # Use the same repo as other GitHub links in this message
                github_links = [l for l in links if l['target_source'] == 'github']
                if github_links:
                    # Extract owner/repo from first GitHub link
                    first_link_id = github_links[0]['target_id']
                    if '/' in first_link_id:
                        parts = first_link_id.split('/')
                        if len(parts) >= 2:
                            owner, repo = parts[0], parts[1]
                            links.append({
                                'target_source': 'github',
                                'target_type': 'issue',
                                'target_id': f'{owner}/{repo}/issues/{issue_num}',
                                'canonical_id': f'github:{owner}/{repo}/issues/{issue_num}',
                                'confidence': 0.7,  # Lower confidence - could be wrong repo
                                'evidence': f'Issue number #{issue_num} found near other GitHub references',
                                'link_text': f'#{issue_num}',
                                'edge_type': EdgeType.DISCUSSES
                            })

        # Jira ticket references (e.g., "PROJ-123", "AUTH-456")
        jira_pattern = r'\b([A-Z]{2,10}-\d+)\b'
        for match in re.finditer(jira_pattern, content):
            ticket_id = match.group(1)
            links.append({
                'target_source': 'jira',
                'target_type': 'issue',
                'target_id': ticket_id,
                'canonical_id': f'jira:{ticket_id}',
                'confidence': 0.8,  # Medium confidence - could be false positive
                'evidence': f'Jira ticket pattern found: {ticket_id}',
                'link_text': ticket_id,
                'edge_type': EdgeType.DISCUSSES
            })

        # Jira URL links
        jira_url_pattern = r'https://([\w.-]+)\.atlassian\.net/browse/([A-Z]+-\d+)'
        for match in re.finditer(jira_url_pattern, content, re.IGNORECASE):
            domain, ticket_id = match.groups()
            links.append({
                'target_source': 'jira',
                'target_type': 'issue',
                'target_id': ticket_id,
                'canonical_id': f'jira:{ticket_id}',
                'confidence': 1.0,
                'evidence': 'Jira ticket URL found in message',
                'link_text': match.group(0),
                'edge_type': EdgeType.DISCUSSES
            })

        # Confluence page links
        confluence_pattern = r'https://([\w.-]+)\.atlassian\.net/wiki/spaces/(\w+)/pages/(\d+)'
        for match in re.finditer(confluence_pattern, content, re.IGNORECASE):
            domain, space, page_id = match.groups()
            links.append({
                'target_source': 'confluence',
                'target_type': 'page',
                'target_id': f'{space}/{page_id}',
                'canonical_id': f'confluence:{space}/{page_id}',
                'confidence': 1.0,
                'evidence': 'Confluence page URL found in message',
                'link_text': match.group(0),
                'edge_type': EdgeType.REFERENCES
            })

        # Slack message links (cross-references to other Slack messages)
        slack_link_pattern = r'https://([\w.-]+)\.slack\.com/archives/(C[\w]+)/p(\d+)'
        for match in re.finditer(slack_link_pattern, content, re.IGNORECASE):
            workspace, channel_id, timestamp = match.groups()
            # Convert timestamp back to Slack format (e.g., 1234567890123456 -> 1234567890.123456)
            ts = f"{timestamp[:10]}.{timestamp[10:]}"
            links.append({
                'target_source': 'slack',
                'target_type': 'message',
                'target_id': ts,
                'canonical_id': f'slack:{ts}',
                'confidence': 1.0,
                'evidence': 'Slack message URL found',
                'link_text': match.group(0),
                'edge_type': EdgeType.REFERENCES
            })

        logger.info(
            "slack_link_detection_completed",
            document_id=document.canonical_id,
            links_detected=len(links)
        )

        return links
