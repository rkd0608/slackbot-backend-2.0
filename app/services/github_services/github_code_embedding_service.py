"""GitHub code embedding service - separate from Slack message code embeddings

This service handles embedding of code from GitHub repositories (ExternalContent model)
while the existing code_embedding_service.py handles Slack message code snippets (CodeSnippet model)
"""
from typing import List, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pinecone import Pinecone
from openai import OpenAI
import httpx
import asyncio
import time

from app.core.config import settings
from app.core.logging import get_logger
from app.models.integration import ExternalContent

logger = get_logger(__name__)


class GitHubCodeEmbeddingService:
    """
    Service for generating embeddings specifically for GitHub code files

    Works with ExternalContent model (GitHub code), NOT CodeSnippet (Slack code)
    Uses dual embedding strategy for better code search
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.pinecone_client = Pinecone(api_key=settings.pinecone_api_key)
        self.github_index = self.pinecone_client.Index(settings.pinecone_github_index_name)
        self.openai_client = OpenAI(api_key=settings.openai_api_key)
        self.voyage_api_key = settings.voyage_api_key if hasattr(settings, 'voyage_api_key') else None

    async def generate_code_embedding_voyage(self, code_text: str, max_retries: int = 3) -> List[float]:
        """Generate code embedding using Voyage AI voyage-code-2 with retry logic"""
        if not self.voyage_api_key:
            logger.warning("voyage_api_key_not_configured_using_openai_fallback")
            return await self.generate_semantic_embedding_openai(code_text)

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.voyageai.com/v1/embeddings",
                        headers={
                            "Authorization": f"Bearer {self.voyage_api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "input": code_text,
                            "model": "voyage-code-2"
                        }
                    )
                    response.raise_for_status()
                    data = response.json()
                    return data['data'][0]['embedding']

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # Rate limit
                    wait_time = (2 ** attempt) * 5  # Exponential backoff: 5s, 10s, 20s
                    logger.warning(
                        "voyage_rate_limit_hit",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        wait_time=wait_time
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        logger.error("voyage_rate_limit_max_retries", fallback="openai")
                        return await self.generate_semantic_embedding_openai(code_text)
                else:
                    logger.error("voyage_embedding_failed", error=str(e), fallback="openai")
                    return await self.generate_semantic_embedding_openai(code_text)
            except Exception as e:
                logger.error("voyage_embedding_failed", error=str(e), fallback="openai")
                return await self.generate_semantic_embedding_openai(code_text)

    async def generate_semantic_embedding_openai(self, text: str, max_retries: int = 3) -> List[float]:
        """Generate semantic embedding using OpenAI with retry logic"""
        for attempt in range(max_retries):
            try:
                response = self.openai_client.embeddings.create(
                    input=text,
                    model=settings.openai_embedding_model,
                    dimensions=1536  # Match Pinecone index dimension
                )
                return response.data[0].embedding

            except Exception as e:
                error_str = str(e)

                # Check if it's a rate limit error (429)
                if "429" in error_str or "rate_limit" in error_str.lower():
                    wait_time = (2 ** attempt) * 5  # Exponential backoff: 5s, 10s, 20s
                    logger.warning(
                        "openai_rate_limit_hit",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        wait_time=wait_time
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        logger.error("openai_rate_limit_max_retries", error=error_str)
                        raise
                else:
                    # Non-rate-limit error, raise immediately
                    logger.error("openai_embedding_failed", error=error_str)
                    raise

    async def embed_github_content(self, content_id: str, team_id: str) -> Dict:
        """
        Generate and store embeddings for a GitHub code file

        Args:
            content_id: ExternalContent ID
            team_id: Workspace team ID

        Returns:
            Dict with embedding stats
        """
        try:
            # Fetch ExternalContent (GitHub code)
            result = await self.db.execute(
                select(ExternalContent).where(
                    and_(
                        ExternalContent.id == content_id,
                        ExternalContent.team_id == team_id,
                        ExternalContent.source_type == "github"
                    )
                )
            )
            content = result.scalar_one_or_none()

            if not content:
                raise ValueError(f"GitHub content {content_id} not found")

            if not content.content:
                raise ValueError(f"Content {content_id} has no text")

            logger.info(
                "github_content_embedding_started",
                content_id=content_id,
                language=content.language,
                size=len(content.content)
            )

            # Generate dual embeddings
            code_embedding = await self.generate_code_embedding_voyage(content.content)
            semantic_embedding = await self.generate_semantic_embedding_openai(content.content)

            # Prepare metadata
            metadata = {
                'content_id': content_id,
                'team_id': team_id,
                'source_type': 'github',
                'source_id': content.source_id or '',
                'source_url': content.source_url or '',
                'title': content.title or '',
                'language': content.language or '',
                'repository_id': content.repository_id or '',
                'repository_full_name': content.repository_full_name or '',
                'repository_visibility': content.repository_visibility or '',
                'visibility': content.visibility,
                'author': content.author or '',
                'indexed_at': content.last_indexed_at.isoformat() if content.last_indexed_at else ''
            }

            # Store in Pinecone with team-specific namespaces
            code_vector_id = f"{content_id}:code"
            semantic_vector_id = f"{content_id}:semantic"
            code_namespace = f"{team_id}:code"
            semantic_namespace = f"{team_id}:semantic"

            # Upsert code embedding
            self.github_index.upsert(
                vectors=[{
                    'id': code_vector_id,
                    'values': code_embedding,
                    'metadata': {**metadata, 'embedding_type': 'code'}
                }],
                namespace=code_namespace
            )

            # Upsert semantic embedding
            self.github_index.upsert(
                vectors=[{
                    'id': semantic_vector_id,
                    'values': semantic_embedding,
                    'metadata': {**metadata, 'embedding_type': 'semantic'}
                }],
                namespace=semantic_namespace
            )

            # Update ExternalContent with vector IDs
            content.vector_id = code_vector_id
            content.vector_id_semantic = semantic_vector_id
            await self.db.commit()

            logger.info(
                "github_content_embedded_successfully",
                content_id=content_id,
                code_vector_id=code_vector_id,
                semantic_vector_id=semantic_vector_id
            )

            return {
                'content_id': content_id,
                'code_vector_id': code_vector_id,
                'semantic_vector_id': semantic_vector_id,
                'code_dimension': len(code_embedding),
                'semantic_dimension': len(semantic_embedding)
            }

        except Exception as e:
            logger.error("github_content_embedding_failed", content_id=content_id, error=str(e))
            raise

    async def batch_embed_github_contents(
        self,
        team_id: str,
        limit: Optional[int] = None,
        delay_between_files: float = 5.0
    ) -> Dict:
        """
        Batch embed all unembedded GitHub contents for a workspace

        Args:
            team_id: Workspace team ID
            limit: Maximum number of files to process
            delay_between_files: Delay in seconds between processing each file (default 5.0s)
                                 This helps prevent rate limiting
        """
        try:
            stmt = select(ExternalContent).where(
                and_(
                    ExternalContent.team_id == team_id,
                    ExternalContent.source_type == "github",
                    ExternalContent.vector_id == None,
                    ExternalContent.is_deleted == False
                )
            )

            if limit:
                stmt = stmt.limit(limit)

            result = await self.db.execute(stmt)
            contents = result.scalars().all()

            logger.info(
                "github_batch_embedding_started",
                team_id=team_id,
                total=len(contents),
                delay_between_files=delay_between_files
            )

            stats = {'total': len(contents), 'succeeded': 0, 'failed': 0, 'errors': []}

            for idx, content in enumerate(contents):
                try:
                    await self.embed_github_content(content.id, team_id)
                    stats['succeeded'] += 1

                    # Add delay between files to prevent rate limiting (except for last file)
                    if idx < len(contents) - 1:
                        await asyncio.sleep(delay_between_files)

                except Exception as e:
                    stats['failed'] += 1
                    stats['errors'].append({'content_id': content.id, 'error': str(e)})

                    # Still add delay even on failure to prevent rate limiting
                    if idx < len(contents) - 1:
                        await asyncio.sleep(delay_between_files)

            logger.info("github_batch_embedding_completed", team_id=team_id, stats=stats)
            return stats

        except Exception as e:
            logger.error("github_batch_embedding_failed", team_id=team_id, error=str(e))
            raise


def get_github_code_embedding_service(db: AsyncSession) -> GitHubCodeEmbeddingService:
    """Get GitHub code embedding service instance"""
    return GitHubCodeEmbeddingService(db)
