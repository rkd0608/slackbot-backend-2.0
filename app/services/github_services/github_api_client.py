"""GitHub API client for fetching repositories, files, and metadata"""
import httpx
from typing import Dict, List, Optional, AsyncGenerator
from datetime import datetime
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class GitHubAPIClient:
    """
    Client for interacting with GitHub API v3

    Handles:
    - Repository listing and metadata
    - File content retrieval
    - Permission extraction
    - Rate limit management
    """

    BASE_URL = settings.github_api_base_url
    API_VERSION = settings.github_api_version

    def __init__(self, access_token: str):
        """
        Initialize GitHub API client

        Args:
            access_token: GitHub OAuth access token
        """
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION
        }

    async def get_user_repos(
        self,
        visibility: str = "all",
        affiliation: str = "owner,collaborator,organization_member",
        sort: str = "updated",
        per_page: int = 100
    ) -> List[Dict]:
        """
        Get repositories accessible to the authenticated user

        Args:
            visibility: Repo visibility filter (all, public, private)
            affiliation: Relationship to repos (owner, collaborator, organization_member)
            sort: Sort order (created, updated, pushed, full_name)
            per_page: Results per page (max 100)

        Returns:
            List of repository dicts with metadata
        """
        try:
            repos = []
            page = 1

            async with httpx.AsyncClient(timeout=30.0) as client:
                while True:
                    response = await client.get(
                        f"{self.BASE_URL}/user/repos",
                        headers=self.headers,
                        params={
                            "visibility": visibility,
                            "affiliation": affiliation,
                            "sort": sort,
                            "per_page": per_page,
                            "page": page
                        }
                    )

                    response.raise_for_status()
                    page_repos = response.json()

                    if not page_repos:
                        break

                    repos.extend(page_repos)

                    # Check rate limit
                    self._log_rate_limit(response.headers)

                    # Check if there are more pages
                    if len(page_repos) < per_page:
                        break

                    page += 1

            logger.info("github_repos_fetched", count=len(repos))
            return repos

        except httpx.HTTPStatusError as e:
            logger.error(
                "github_repos_fetch_failed",
                status_code=e.response.status_code,
                error=str(e)
            )
            raise
        except Exception as e:
            logger.error("github_repos_fetch_error", error=str(e))
            raise

    async def get_repo_contents(
        self,
        owner: str,
        repo: str,
        path: str = ""
    ) -> List[Dict]:
        """
        Get contents of a repository directory

        Args:
            owner: Repository owner
            repo: Repository name
            path: Path within repo (empty for root)

        Returns:
            List of file/directory metadata dicts
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}",
                    headers=self.headers
                )

                response.raise_for_status()
                contents = response.json()

                # Handle single file vs directory
                if isinstance(contents, dict):
                    contents = [contents]

                self._log_rate_limit(response.headers)

                logger.info(
                    "github_repo_contents_fetched",
                    owner=owner,
                    repo=repo,
                    path=path,
                    count=len(contents)
                )

                return contents

        except httpx.HTTPStatusError as e:
            logger.error(
                "github_contents_fetch_failed",
                owner=owner,
                repo=repo,
                path=path,
                status_code=e.response.status_code
            )
            raise
        except Exception as e:
            logger.error("github_contents_fetch_error", error=str(e))
            raise

    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: Optional[str] = None
    ) -> Dict:
        """
        Get raw content of a file

        Args:
            owner: Repository owner
            repo: Repository name
            path: File path within repo
            ref: Git ref (branch, tag, commit SHA) - defaults to default branch

        Returns:
            Dict with file metadata and decoded content
        """
        try:
            params = {}
            if ref:
                params["ref"] = ref

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}",
                    headers=self.headers,
                    params=params
                )

                response.raise_for_status()
                file_data = response.json()

                # Decode base64 content
                import base64
                if "content" in file_data and file_data.get("encoding") == "base64":
                    file_data["decoded_content"] = base64.b64decode(
                        file_data["content"]
                    ).decode("utf-8", errors="ignore")

                self._log_rate_limit(response.headers)

                logger.info(
                    "github_file_content_fetched",
                    owner=owner,
                    repo=repo,
                    path=path,
                    size=file_data.get("size", 0)
                )

                return file_data

        except httpx.HTTPStatusError as e:
            logger.error(
                "github_file_fetch_failed",
                owner=owner,
                repo=repo,
                path=path,
                status_code=e.response.status_code
            )
            raise
        except Exception as e:
            logger.error("github_file_fetch_error", error=str(e))
            raise

    async def get_repo_tree(
        self,
        owner: str,
        repo: str,
        tree_sha: str = "HEAD",
        recursive: bool = True
    ) -> Dict:
        """
        Get full repository tree (file structure)

        Args:
            owner: Repository owner
            repo: Repository name
            tree_sha: SHA of tree (or "HEAD" for default branch)
            recursive: Get full tree recursively

        Returns:
            Tree data with all files and directories
        """
        try:
            params = {}
            if recursive:
                params["recursive"] = "1"

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/git/trees/{tree_sha}",
                    headers=self.headers,
                    params=params
                )

                response.raise_for_status()
                tree_data = response.json()

                self._log_rate_limit(response.headers)

                logger.info(
                    "github_repo_tree_fetched",
                    owner=owner,
                    repo=repo,
                    file_count=len(tree_data.get("tree", []))
                )

                return tree_data

        except httpx.HTTPStatusError as e:
            logger.error(
                "github_tree_fetch_failed",
                owner=owner,
                repo=repo,
                status_code=e.response.status_code
            )
            raise
        except Exception as e:
            logger.error("github_tree_fetch_error", error=str(e))
            raise

    async def get_repo_collaborators(
        self,
        owner: str,
        repo: str
    ) -> List[Dict]:
        """
        Get repository collaborators with permissions

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            List of collaborators with permission levels
        """
        try:
            collaborators = []
            page = 1
            per_page = 100

            async with httpx.AsyncClient(timeout=30.0) as client:
                while True:
                    response = await client.get(
                        f"{self.BASE_URL}/repos/{owner}/{repo}/collaborators",
                        headers=self.headers,
                        params={"per_page": per_page, "page": page}
                    )

                    response.raise_for_status()
                    page_collaborators = response.json()

                    if not page_collaborators:
                        break

                    collaborators.extend(page_collaborators)

                    self._log_rate_limit(response.headers)

                    if len(page_collaborators) < per_page:
                        break

                    page += 1

            logger.info(
                "github_collaborators_fetched",
                owner=owner,
                repo=repo,
                count=len(collaborators)
            )

            return collaborators

        except httpx.HTTPStatusError as e:
            logger.error(
                "github_collaborators_fetch_failed",
                owner=owner,
                repo=repo,
                status_code=e.response.status_code
            )
            raise
        except Exception as e:
            logger.error("github_collaborators_fetch_error", error=str(e))
            raise

    async def get_repo_languages(
        self,
        owner: str,
        repo: str
    ) -> Dict[str, int]:
        """
        Get programming languages used in repository

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            Dict mapping language to bytes of code
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/languages",
                    headers=self.headers
                )

                response.raise_for_status()
                languages = response.json()

                self._log_rate_limit(response.headers)

                logger.info(
                    "github_languages_fetched",
                    owner=owner,
                    repo=repo,
                    languages=list(languages.keys())
                )

                return languages

        except httpx.HTTPStatusError as e:
            logger.error(
                "github_languages_fetch_failed",
                owner=owner,
                repo=repo,
                status_code=e.response.status_code
            )
            raise
        except Exception as e:
            logger.error("github_languages_fetch_error", error=str(e))
            raise

    async def get_rate_limit(self) -> Dict:
        """
        Get current rate limit status

        Returns:
            Rate limit info including remaining requests
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/rate_limit",
                    headers=self.headers
                )

                response.raise_for_status()
                rate_limit_data = response.json()

                logger.info(
                    "github_rate_limit_checked",
                    remaining=rate_limit_data["rate"]["remaining"],
                    limit=rate_limit_data["rate"]["limit"]
                )

                return rate_limit_data

        except Exception as e:
            logger.error("github_rate_limit_check_failed", error=str(e))
            raise

    def _log_rate_limit(self, headers: Dict):
        """Log rate limit info from response headers"""
        remaining = headers.get("X-RateLimit-Remaining")
        limit = headers.get("X-RateLimit-Limit")
        reset_timestamp = headers.get("X-RateLimit-Reset")

        if remaining and limit:
            logger.debug(
                "github_rate_limit",
                remaining=int(remaining),
                limit=int(limit),
                reset_at=reset_timestamp
            )

            # Warn if running low
            if int(remaining) < 100:
                logger.warning(
                    "github_rate_limit_low",
                    remaining=int(remaining),
                    reset_at=reset_timestamp
                )

    async def search_code(
        self,
        query: str,
        per_page: int = 30
    ) -> Dict:
        """
        Search for code across all accessible repositories

        Args:
            query: Search query (supports GitHub code search syntax)
            per_page: Results per page (max 100)

        Returns:
            Search results with file matches
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/search/code",
                    headers=self.headers,
                    params={"q": query, "per_page": per_page}
                )

                response.raise_for_status()
                search_results = response.json()

                self._log_rate_limit(response.headers)

                logger.info(
                    "github_code_search_completed",
                    query=query,
                    total_count=search_results.get("total_count", 0)
                )

                return search_results

        except httpx.HTTPStatusError as e:
            logger.error(
                "github_code_search_failed",
                query=query,
                status_code=e.response.status_code
            )
            raise
        except Exception as e:
            logger.error("github_code_search_error", error=str(e))
            raise


def get_github_client(access_token: str) -> GitHubAPIClient:
    """
    Create GitHub API client instance

    Args:
        access_token: GitHub OAuth access token

    Returns:
        Initialized GitHubAPIClient
    """
    return GitHubAPIClient(access_token)
