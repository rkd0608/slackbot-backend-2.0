"""Permission-aware GitHub code retrieval service

This service searches GitHub code with permission filtering to ensure
users only see code they have access to.
"""
from typing import List, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pinecone import Pinecone

from app.core.config import settings
from app.core.logging import get_logger
from app.models.integration import ExternalContent, UserIntegrationConnection
from app.services.github_services.github_code_embedding_service import get_github_code_embedding_service

logger = get_logger(__name__)


class GitHubRetrievalService:
    """
    Service for permission-aware GitHub code retrieval

    Ensures users only see code from repositories they have access to
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.pinecone_client = Pinecone(api_key=settings.pinecone_api_key)
        self.github_index = self.pinecone_client.Index(settings.pinecone_github_index_name)
        self.embedding_service = get_github_code_embedding_service(db)

    async def search_github_code(
        self,
        query: str,
        user_id: str,
        team_id: str,
        top_k: int = 10,
        language_filter: Optional[str] = None,
        repo_filter: Optional[str] = None,
        search_type: str = "hybrid"
    ) -> List[Dict]:
        """
        Search GitHub code with permission filtering

        Args:
            query: Search query
            user_id: User ID (for permission checking)
            team_id: Workspace team ID
            top_k: Number of results to return
            language_filter: Optional language filter (e.g., "python")
            repo_filter: Optional repository filter (e.g., "facebook/react")
            search_type: "code", "semantic", or "hybrid"

        Returns:
            List of code results with content and metadata
        """
        try:
            # Get user's GitHub connection to extract external_user_id
            result = await self.db.execute(
                select(UserIntegrationConnection).where(
                    and_(
                        UserIntegrationConnection.user_id == user_id,
                        UserIntegrationConnection.team_id == team_id,
                        UserIntegrationConnection.integration_type == "github",
                        UserIntegrationConnection.is_active == True
                    )
                )
            )
            connection = result.scalar_one_or_none()

            external_user_id = connection.external_user_id if connection else None

            logger.info(
                "github_code_search_started",
                user_id=user_id,
                team_id=team_id,
                has_connection=bool(connection),
                external_user_id=external_user_id
            )

            # Build metadata filter for Pinecone
            metadata_filter = self._build_permission_filter(
                team_id=team_id,
                external_user_id=external_user_id,
                language=language_filter,
                repo=repo_filter
            )

            # Search using hybrid approach
            results = []

            if search_type in ["code", "hybrid"]:
                # Code-based search
                code_embedding = await self.embedding_service.generate_code_embedding_voyage(query)
                code_namespace = f"{team_id}:code"

                code_results = self.github_index.query(
                    vector=code_embedding,
                    top_k=top_k * 2,  # Get more for filtering
                    namespace=code_namespace,
                    filter=metadata_filter,
                    include_metadata=True
                )

                for match in code_results.matches:
                    results.append({
                        'id': match.id,
                        'score': match.score,
                        'metadata': match.metadata,
                        'embedding_type': 'code'
                    })

            if search_type in ["semantic", "hybrid"]:
                # Semantic search
                semantic_embedding = await self.embedding_service.generate_semantic_embedding_openai(query)
                semantic_namespace = f"{team_id}:semantic"

                semantic_results = self.github_index.query(
                    vector=semantic_embedding,
                    top_k=top_k * 2,
                    namespace=semantic_namespace,
                    filter=metadata_filter,
                    include_metadata=True
                )

                for match in semantic_results.matches:
                    results.append({
                        'id': match.id,
                        'score': match.score,
                        'metadata': match.metadata,
                        'embedding_type': 'semantic'
                    })

            # Merge and rank results if hybrid
            if search_type == "hybrid":
                results = self._merge_hybrid_results(results, top_k)
            else:
                results = sorted(results, key=lambda x: x['score'], reverse=True)[:top_k]

            # Fetch full content from database with permission check
            enriched_results = await self._enrich_with_content(
                results=results,
                team_id=team_id,
                external_user_id=external_user_id
            )

            logger.info(
                "github_code_search_completed",
                user_id=user_id,
                results_count=len(enriched_results),
                search_type=search_type
            )

            return enriched_results

        except Exception as e:
            logger.error(
                "github_code_search_failed",
                user_id=user_id,
                error=str(e)
            )
            raise

    def _build_permission_filter(
        self,
        team_id: str,
        external_user_id: Optional[str],
        language: Optional[str],
        repo: Optional[str]
    ) -> Dict:
        """
        Build Pinecone metadata filter for permission-aware search

        Filter logic:
        - Always filter by team_id
        - Include public repos OR repos user has access to
        - Optional language and repo filters
        """
        filter_dict = {
            "team_id": team_id
        }

        # Permission filtering: public OR (private AND user has access)
        # In Pinecone, we use $or for this
        if external_user_id:
            # User has GitHub connection - can see public + their private repos
            filter_dict["$or"] = [
                {"repository_visibility": "public"},
                {"repository_visibility": "private"}  # We'll do additional DB check later
            ]
        else:
            # No GitHub connection - only public repos
            filter_dict["repository_visibility"] = "public"

        # Language filter
        if language:
            filter_dict["language"] = language.lower()

        # Repository filter
        if repo:
            filter_dict["repository_full_name"] = repo

        return filter_dict

    async def _enrich_with_content(
        self,
        results: List[Dict],
        team_id: str,
        external_user_id: Optional[str]
    ) -> List[Dict]:
        """
        Enrich vector search results with full content from database

        Performs additional permission check at database level
        """
        enriched_results = []

        for result in results:
            content_id = result['metadata']['content_id']

            # Fetch from database
            db_result = await self.db.execute(
                select(ExternalContent).where(
                    and_(
                        ExternalContent.id == content_id,
                        ExternalContent.team_id == team_id,
                        ExternalContent.is_deleted == False
                    )
                )
            )
            content = db_result.scalar_one_or_none()

            if not content:
                continue

            # Additional permission check at database level
            if not self._user_has_access(content, external_user_id):
                logger.debug(
                    "permission_denied_skipping_result",
                    content_id=content_id,
                    visibility=content.visibility
                )
                continue

            # Build enriched result
            enriched_results.append({
                'content_id': content.id,
                'score': result['score'],
                'embedding_type': result.get('embedding_type', 'hybrid'),
                'title': content.title,
                'file_path': result['metadata'].get('title', ''),
                'repository': content.repository_full_name,
                'repository_visibility': content.repository_visibility,
                'language': content.language,
                'author': content.author,
                'source_url': content.source_url,
                'content': content.content,  # Full code content
                'created_at': content.created_at_source.isoformat() if content.created_at_source else None,
                'updated_at': content.updated_at_source.isoformat() if content.updated_at_source else None,
                'indexed_at': content.last_indexed_at.isoformat() if content.last_indexed_at else None
            })

        return enriched_results

    def _user_has_access(
        self,
        content: ExternalContent,
        external_user_id: Optional[str]
    ) -> bool:
        """
        Check if user has access to a piece of content

        Access rules:
        1. Public repos - everyone has access
        2. Private repos - only accessible_by_user_ids have access
        """
        # Public repos are always accessible
        if content.visibility == "public" or content.repository_visibility == "public":
            return True

        # No GitHub connection - can't access private repos
        if not external_user_id:
            return False

        # Check if user is in accessible_by_user_ids
        if content.accessible_by_user_ids:
            return external_user_id in content.accessible_by_user_ids

        return False

    def _merge_hybrid_results(
        self,
        results: List[Dict],
        top_k: int
    ) -> List[Dict]:
        """
        Merge code and semantic search results with weighted scoring

        Uses the configured weights from settings
        """
        from collections import defaultdict

        # Group by content_id
        content_scores = defaultdict(lambda: {'code': 0.0, 'semantic': 0.0, 'metadata': None})

        for result in results:
            content_id = result['metadata']['content_id']
            embedding_type = result['embedding_type']
            score = result['score']

            content_scores[content_id][embedding_type] = max(
                content_scores[content_id][embedding_type],
                score
            )

            if content_scores[content_id]['metadata'] is None:
                content_scores[content_id]['metadata'] = result['metadata']

        # Calculate combined scores
        code_weight = getattr(settings, 'code_embedding_weight', 0.6)
        semantic_weight = getattr(settings, 'semantic_embedding_weight', 0.4)

        merged_results = []
        for content_id, scores in content_scores.items():
            combined_score = (
                scores['code'] * code_weight +
                scores['semantic'] * semantic_weight
            )

            merged_results.append({
                'id': content_id,
                'score': combined_score,
                'code_score': scores['code'],
                'semantic_score': scores['semantic'],
                'metadata': scores['metadata'],
                'embedding_type': 'hybrid'
            })

        # Sort by combined score and return top_k
        merged_results.sort(key=lambda x: x['score'], reverse=True)
        return merged_results[:top_k]


def get_github_retrieval_service(db: AsyncSession) -> GitHubRetrievalService:
    """Get GitHub retrieval service instance"""
    return GitHubRetrievalService(db)
