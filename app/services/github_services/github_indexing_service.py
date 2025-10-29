"""GitHub repository indexing service with permission-aware embedding"""
from typing import Dict, List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
import uuid
import hashlib

from app.core.logging import get_logger
from app.services.github_services.github_api_client import get_github_client
from app.services.github_services.github_oauth_service import get_github_oauth_service
from app.models.integration import (
    ExternalContent,
    IntegrationSyncJob,
    UserIntegrationConnection
)

logger = get_logger(__name__)


class GitHubIndexingService:
    """
    Service for indexing GitHub repositories

    Features:
    - Repository crawling with file filtering
    - Permission extraction and tracking
    - Dual embedding generation (code + semantic)
    - Incremental updates
    - Progress tracking via sync jobs
    """

    # Supported code file extensions
    CODE_EXTENSIONS = {
        '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rs',
        '.cpp', '.c', '.h', '.hpp', '.cs', '.rb', '.php', '.swift',
        '.kt', '.scala', '.sql', '.sh', '.yaml', '.yml', '.json',
        '.md', '.txt', '.rst'
    }

    # Files to skip
    SKIP_PATTERNS = {
        'node_modules', '.git', '__pycache__', '.venv', 'venv',
        'dist', 'build', '.next', '.cache', 'vendor', 'target'
    }

    # Maximum file size to index (1MB)
    MAX_FILE_SIZE = 1024 * 1024

    def __init__(self, db: AsyncSession):
        self.db = db
        self.github_oauth_service = get_github_oauth_service()

    async def index_repository(
        self,
        connection: UserIntegrationConnection,
        repo_owner: str,
        repo_name: str,
        sync_job_id: Optional[str] = None
    ) -> Dict:
        """
        Index a GitHub repository

        Args:
            connection: User's GitHub connection with access token
            repo_owner: Repository owner (user or org)
            repo_name: Repository name
            sync_job_id: Optional sync job ID for progress tracking

        Returns:
            Dict with indexing statistics
        """
        try:
            # Get decrypted access token
            access_token = self.github_oauth_service.get_decrypted_token(connection)

            # Create GitHub API client
            github_client = get_github_client(access_token)

            logger.info(
                "github_repo_indexing_started",
                repo=f"{repo_owner}/{repo_name}",
                user_id=connection.user_id,
                team_id=connection.team_id
            )

            # Update sync job status
            sync_job = None
            if sync_job_id:
                result = await self.db.execute(
                    select(IntegrationSyncJob).where(
                        IntegrationSyncJob.id == sync_job_id
                    )
                )
                sync_job = result.scalar_one_or_none()
                if sync_job:
                    sync_job.status = "running"
                    sync_job.started_at = datetime.utcnow()
                    await self.db.commit()

            stats = {
                'files_processed': 0,
                'files_indexed': 0,
                'files_skipped': 0,
                'files_updated': 0,
                'errors': []
            }

            # Fetch repository metadata
            repos = await github_client.get_user_repos()
            repo_data = next(
                (r for r in repos if r['owner']['login'] == repo_owner and r['name'] == repo_name),
                None
            )

            if not repo_data:
                raise ValueError(f"Repository {repo_owner}/{repo_name} not found or not accessible")

            # Extract repository permissions
            repo_permissions = self._extract_repo_permissions(repo_data, connection)

            # Get repository tree (all files)
            tree_data = await github_client.get_repo_tree(
                owner=repo_owner,
                repo=repo_name,
                tree_sha="HEAD",
                recursive=True
            )

            # Filter to code files only
            code_files = self._filter_code_files(tree_data.get('tree', []))

            if sync_job:
                sync_job.total_items = len(code_files)
                await self.db.commit()

            logger.info(
                "github_code_files_found",
                repo=f"{repo_owner}/{repo_name}",
                total_files=len(code_files)
            )

            # Process each file
            for file_item in code_files:
                try:
                    stats['files_processed'] += 1

                    # Fetch file content
                    file_data = await github_client.get_file_content(
                        owner=repo_owner,
                        repo=repo_name,
                        path=file_item['path']
                    )

                    # Check file size
                    if file_data.get('size', 0) > self.MAX_FILE_SIZE:
                        stats['files_skipped'] += 1
                        logger.debug(
                            "github_file_too_large",
                            path=file_item['path'],
                            size=file_data['size']
                        )
                        continue

                    # Get decoded content
                    content = file_data.get('decoded_content', '')
                    if not content:
                        stats['files_skipped'] += 1
                        continue

                    # Detect language
                    language = self._detect_language(file_item['path'])

                    # Create or update ExternalContent
                    result = await self._index_file(
                        connection=connection,
                        repo_data=repo_data,
                        file_item=file_item,
                        file_content=content,
                        language=language,
                        permissions=repo_permissions
                    )

                    if result == 'created':
                        stats['files_indexed'] += 1
                    elif result == 'updated':
                        stats['files_updated'] += 1

                    # Update sync job progress
                    if sync_job:
                        sync_job.processed_items = stats['files_processed']
                        sync_job.items_created = stats['files_indexed']
                        sync_job.items_updated = stats['files_updated']
                        await self.db.commit()

                except Exception as e:
                    stats['errors'].append({
                        'file': file_item['path'],
                        'error': str(e)
                    })
                    logger.error(
                        "github_file_indexing_failed",
                        path=file_item['path'],
                        error=str(e)
                    )

                    if sync_job:
                        sync_job.failed_items += 1
                        await self.db.commit()

            # Complete sync job
            if sync_job:
                sync_job.status = "completed"
                sync_job.completed_at = datetime.utcnow()
                await self.db.commit()

            logger.info(
                "github_repo_indexing_completed",
                repo=f"{repo_owner}/{repo_name}",
                stats=stats
            )

            return stats

        except Exception as e:
            logger.error(
                "github_repo_indexing_failed",
                repo=f"{repo_owner}/{repo_name}",
                error=str(e)
            )

            if sync_job:
                sync_job.status = "failed"
                sync_job.error_message = str(e)
                sync_job.completed_at = datetime.utcnow()
                await self.db.commit()

            raise

    async def _index_file(
        self,
        connection: UserIntegrationConnection,
        repo_data: Dict,
        file_item: Dict,
        file_content: str,
        language: str,
        permissions: Dict
    ) -> str:
        """
        Index a single file to database (embeddings will be generated separately)

        Returns:
            'created' or 'updated'
        """
        # Generate unique source_id
        source_id = f"github:{repo_data['owner']['login']}/{repo_data['name']}:{file_item['path']}"

        # Check if file already exists
        result = await self.db.execute(
            select(ExternalContent).where(
                and_(
                    ExternalContent.source_type == "github",
                    ExternalContent.source_id == source_id
                )
            )
        )
        existing_content = result.scalar_one_or_none()

        # Generate content hash for change detection
        content_hash = hashlib.sha256(file_content.encode('utf-8')).hexdigest()

        if existing_content:
            # Check if content changed
            if existing_content.source_metadata and \
               existing_content.source_metadata.get('content_hash') == content_hash:
                return 'skipped'

            # Update existing
            existing_content.content = file_content
            existing_content.updated_at_source = datetime.utcnow()
            existing_content.language = language
            existing_content.last_indexed_at = datetime.utcnow()
            existing_content.updated_at = datetime.utcnow()
            existing_content.source_metadata = {
                'sha': file_item.get('sha'),
                'content_hash': content_hash,
                'size': file_item.get('size')
            }

            await self.db.commit()
            await self.db.refresh(existing_content)

            logger.debug(
                "github_file_updated",
                source_id=source_id
            )

            return 'updated'

        # Create new content record
        content_record = ExternalContent(
            id=str(uuid.uuid4()),
            team_id=connection.team_id,
            source_type="github",
            source_id=source_id,
            source_url=f"https://github.com/{repo_data['owner']['login']}/{repo_data['name']}/blob/{repo_data['default_branch']}/{file_item['path']}",
            title=file_item['path'].split('/')[-1],
            content=file_content,
            content_type="code",
            language=language,
            author=repo_data['owner']['login'],
            author_external_id=str(repo_data['owner']['id']),
            created_at_source=datetime.fromisoformat(repo_data['created_at'].replace('Z', '+00:00')),
            updated_at_source=datetime.fromisoformat(repo_data['updated_at'].replace('Z', '+00:00')),
            source_metadata={
                'sha': file_item.get('sha'),
                'content_hash': content_hash,
                'size': file_item.get('size'),
                'repo_stars': repo_data.get('stargazers_count', 0),
                'repo_forks': repo_data.get('forks_count', 0)
            },
            visibility=permissions['visibility'],
            accessible_by_user_ids=permissions['accessible_by_user_ids'],
            accessible_by_team_ids=permissions['accessible_by_team_ids'],
            repository_id=str(repo_data['id']),
            repository_full_name=repo_data['full_name'],
            repository_visibility=repo_data.get('visibility', 'private'),
            indexed_by_user_id=connection.user_id,
            last_indexed_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        self.db.add(content_record)
        await self.db.commit()
        await self.db.refresh(content_record)

        logger.debug(
            "github_file_indexed",
            source_id=source_id,
            content_id=content_record.id
        )

        return 'created'

    def _extract_repo_permissions(
        self,
        repo_data: Dict,
        connection: UserIntegrationConnection
    ) -> Dict:
        """
        Extract permission metadata from repository

        Returns:
            Dict with visibility and accessible_by lists
        """
        visibility = "private"
        accessible_by_user_ids = []
        accessible_by_team_ids = []

        # Check if repo is public
        if repo_data.get('private') is False:
            visibility = "public"
        elif repo_data.get('visibility') == 'public':
            visibility = "public"

        # Add repository owner
        accessible_by_user_ids.append(str(repo_data['owner']['id']))

        # Add the connecting user if different
        if connection.external_user_id and connection.external_user_id != str(repo_data['owner']['id']):
            accessible_by_user_ids.append(connection.external_user_id)

        # If repo belongs to an org, track org ID
        if repo_data['owner']['type'] == 'Organization':
            accessible_by_team_ids.append(str(repo_data['owner']['id']))

        return {
            'visibility': visibility,
            'accessible_by_user_ids': accessible_by_user_ids,
            'accessible_by_team_ids': accessible_by_team_ids
        }

    def _filter_code_files(self, tree_items: List[Dict]) -> List[Dict]:
        """Filter tree items to only include indexable code files"""
        code_files = []

        for item in tree_items:
            # Skip directories
            if item.get('type') != 'blob':
                continue

            path = item.get('path', '')

            # Skip if path contains excluded patterns
            if any(pattern in path for pattern in self.SKIP_PATTERNS):
                continue

            # Check file extension
            extension = '.' + path.split('.')[-1] if '.' in path else ''
            if extension.lower() in self.CODE_EXTENSIONS or path.endswith('.md'):
                code_files.append(item)

        return code_files

    def _detect_language(self, file_path: str) -> Optional[str]:
        """Detect programming language from file extension"""
        extension_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'javascript',
            '.tsx': 'typescript',
            '.java': 'java',
            '.go': 'go',
            '.rs': 'rust',
            '.cpp': 'cpp',
            '.c': 'c',
            '.h': 'c',
            '.hpp': 'cpp',
            '.cs': 'csharp',
            '.rb': 'ruby',
            '.php': 'php',
            '.swift': 'swift',
            '.kt': 'kotlin',
            '.scala': 'scala',
            '.sql': 'sql',
            '.sh': 'shell',
            '.yaml': 'yaml',
            '.yml': 'yaml',
            '.json': 'json',
            '.md': 'markdown',
            '.txt': 'text',
            '.rst': 'restructuredtext'
        }

        extension = '.' + file_path.split('.')[-1] if '.' in file_path else ''
        return extension_map.get(extension.lower())

    async def list_user_repos(
        self,
        connection: UserIntegrationConnection
    ) -> List[Dict]:
        """
        List all repositories accessible to user

        Args:
            connection: User's GitHub connection

        Returns:
            List of repository metadata
        """
        try:
            access_token = self.github_oauth_service.get_decrypted_token(connection)
            github_client = get_github_client(access_token)

            repos = await github_client.get_user_repos(
                visibility="all",
                sort="updated"
            )

            logger.info(
                "github_user_repos_listed",
                user_id=connection.user_id,
                count=len(repos)
            )

            return repos

        except Exception as e:
            logger.error(
                "github_user_repos_list_failed",
                user_id=connection.user_id,
                error=str(e)
            )
            raise

    async def get_indexed_repos(
        self,
        team_id: str
    ) -> List[Dict]:
        """
        Get list of repositories that have been indexed for a workspace

        Args:
            team_id: Workspace team ID

        Returns:
            List of unique repository info
        """
        stmt = select(
            ExternalContent.repository_full_name,
            ExternalContent.repository_id,
            ExternalContent.repository_visibility,
            func.count(ExternalContent.id).label('file_count'),
            func.max(ExternalContent.last_indexed_at).label('last_indexed_at')
        ).where(
            and_(
                ExternalContent.team_id == team_id,
                ExternalContent.source_type == "github",
                ExternalContent.is_deleted == False
            )
        ).group_by(
            ExternalContent.repository_full_name,
            ExternalContent.repository_id,
            ExternalContent.repository_visibility
        )

        result = await self.db.execute(stmt)
        repos = result.all()

        return [
            {
                'full_name': r.repository_full_name,
                'repository_id': r.repository_id,
                'visibility': r.repository_visibility,
                'file_count': r.file_count,
                'last_indexed_at': r.last_indexed_at.isoformat() if r.last_indexed_at else None
            }
            for r in repos
        ]


def get_github_indexing_service(db: AsyncSession) -> GitHubIndexingService:
    """Get GitHub indexing service instance"""
    return GitHubIndexingService(db)
