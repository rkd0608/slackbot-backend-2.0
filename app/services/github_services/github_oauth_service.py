"""GitHub OAuth service for managing user connections"""
import httpx
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.core.config import settings
from app.core.logging import get_logger
from app.core.encryption import get_encryption_service
from app.models.integration import Integration, UserIntegrationConnection
from app.models.user import User
import uuid

logger = get_logger(__name__)


class GitHubOAuthService:
    """
    Handles GitHub OAuth 2.0 flow for user authentication

    OAuth Flow:
    1. Generate authorization URL with scopes
    2. User authorizes on GitHub
    3. GitHub redirects with code
    4. Exchange code for access token
    5. Fetch user info from GitHub
    6. Store encrypted token in database
    """

    GITHUB_OAUTH_URL = "https://github.com/login/oauth/authorize"
    GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
    GITHUB_USER_URL = "https://api.github.com/user"

    # Scopes needed for code indexing
    DEFAULT_SCOPES = [
        "repo",           # Access private repos
        "read:org",       # Read org membership
        "read:user",      # Read user profile
        "user:email"      # Read user email
    ]

    def __init__(self):
        self.client_id = settings.github_client_id
        self.client_secret = settings.github_client_secret
        self.redirect_uri = settings.github_oauth_redirect_uri
        self.encryption_service = get_encryption_service()

    def generate_authorization_url(self, state: str, scopes: Optional[list] = None) -> str:
        """
        Generate GitHub OAuth authorization URL

        Args:
            state: CSRF protection token (should be stored in session)
            scopes: List of OAuth scopes (defaults to DEFAULT_SCOPES)

        Returns:
            Authorization URL to redirect user to
        """
        scopes = scopes or self.DEFAULT_SCOPES
        scope_string = " ".join(scopes)

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope_string,
            "state": state
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        auth_url = f"{self.GITHUB_OAUTH_URL}?{query_string}"

        logger.info("github_auth_url_generated", scopes=scopes)
        return auth_url

    async def exchange_code_for_token(self, code: str) -> Dict:
        """
        Exchange authorization code for access token

        Args:
            code: Authorization code from GitHub redirect

        Returns:
            Dict with access_token, token_type, scope

        Raises:
            Exception if token exchange fails
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.GITHUB_TOKEN_URL,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "code": code,
                        "redirect_uri": self.redirect_uri
                    },
                    headers={
                        "Accept": "application/json"
                    }
                )

                response.raise_for_status()
                token_data = response.json()

                if "error" in token_data:
                    raise Exception(f"GitHub OAuth error: {token_data.get('error_description', token_data['error'])}")

                logger.info("github_token_exchanged", scope=token_data.get("scope"))
                return token_data

        except Exception as e:
            logger.error("github_token_exchange_failed", error=str(e))
            raise

    async def get_user_info(self, access_token: str) -> Dict:
        """
        Fetch GitHub user information using access token

        Args:
            access_token: GitHub access token

        Returns:
            Dict with user info (id, login, email, name, etc.)
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.GITHUB_USER_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": settings.github_api_version
                    }
                )

                response.raise_for_status()
                user_info = response.json()

                logger.info("github_user_info_fetched", github_user_id=user_info.get("id"))
                return user_info

        except Exception as e:
            logger.error("github_user_info_fetch_failed", error=str(e))
            raise

    async def store_connection(
        self,
        db: AsyncSession,
        user_id: str,
        team_id: str,
        integration_id: str,
        access_token: str,
        github_user_info: Dict,
        scopes: list
    ) -> UserIntegrationConnection:
        """
        Store encrypted GitHub connection in database

        Args:
            db: Database session
            user_id: Slack user ID
            team_id: Slack workspace team ID
            integration_id: Integration record ID
            access_token: GitHub access token
            github_user_info: User info from GitHub API
            scopes: List of granted OAuth scopes

        Returns:
            Created UserIntegrationConnection
        """
        try:
            # Encrypt the access token
            encrypted_token = self.encryption_service.encrypt(access_token)

            # Check if connection already exists
            result = await db.execute(
                select(UserIntegrationConnection).where(
                    and_(
                        UserIntegrationConnection.user_id == user_id,
                        UserIntegrationConnection.integration_id == integration_id,
                        UserIntegrationConnection.integration_type == "github"
                    )
                )
            )
            existing_connection = result.scalar_one_or_none()

            if existing_connection:
                # Update existing connection
                existing_connection.access_token_encrypted = encrypted_token
                existing_connection.external_user_id = str(github_user_info["id"])
                existing_connection.external_username = github_user_info["login"]
                existing_connection.external_email = github_user_info.get("email")
                existing_connection.scopes = scopes
                existing_connection.is_active = True
                existing_connection.connected_at = datetime.utcnow()
                existing_connection.disconnected_at = None
                existing_connection.error_count = 0
                existing_connection.last_error = None
                existing_connection.updated_at = datetime.utcnow()

                await db.commit()
                await db.refresh(existing_connection)

                logger.info(
                    "github_connection_updated",
                    user_id=user_id,
                    github_user_id=github_user_info["id"]
                )
                return existing_connection

            # Create new connection
            connection = UserIntegrationConnection(
                id=str(uuid.uuid4()),
                user_id=user_id,
                team_id=team_id,
                integration_id=integration_id,
                integration_type="github",
                access_token_encrypted=encrypted_token,
                refresh_token_encrypted=None,  # GitHub doesn't use refresh tokens
                token_expires_at=None,  # GitHub tokens don't expire
                external_user_id=str(github_user_info["id"]),
                external_username=github_user_info["login"],
                external_email=github_user_info.get("email"),
                scopes=scopes,
                is_active=True,
                connected_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )

            db.add(connection)
            await db.commit()
            await db.refresh(connection)

            logger.info(
                "github_connection_created",
                user_id=user_id,
                github_user_id=github_user_info["id"],
                connection_id=connection.id
            )
            return connection

        except Exception as e:
            await db.rollback()
            logger.error("github_connection_store_failed", error=str(e))
            raise

    async def get_user_connection(
        self,
        db: AsyncSession,
        user_id: str,
        team_id: str
    ) -> Optional[UserIntegrationConnection]:
        """
        Get active GitHub connection for user

        Args:
            db: Database session
            user_id: Slack user ID
            team_id: Slack workspace team ID

        Returns:
            UserIntegrationConnection if exists and active, else None
        """
        result = await db.execute(
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

        return connection

    def get_decrypted_token(self, connection: UserIntegrationConnection) -> str:
        """
        Decrypt and return GitHub access token

        Args:
            connection: UserIntegrationConnection with encrypted token

        Returns:
            Decrypted access token
        """
        try:
            return self.encryption_service.decrypt(connection.access_token_encrypted)
        except Exception as e:
            logger.error(
                "token_decryption_failed",
                connection_id=connection.id,
                error=str(e)
            )
            raise

    async def disconnect_user(
        self,
        db: AsyncSession,
        user_id: str,
        team_id: str,
        delete_all_data: bool = True
    ) -> Dict:
        """
        Disconnect GitHub integration for user and delete all associated data

        Args:
            db: Database session
            user_id: Slack user ID
            team_id: Slack workspace team ID
            delete_all_data: If True, deletes all indexed repos, embeddings, and sync jobs

        Returns:
            Dict with deletion stats
        """
        from app.models.integration import ExternalContent, IntegrationSyncJob
        from sqlalchemy import delete as sql_delete
        from pinecone import Pinecone

        connection = await self.get_user_connection(db, user_id, team_id)

        if not connection:
            return {
                'success': False,
                'message': 'No connection found',
                'stats': {}
            }

        stats = {
            'external_content_deleted': 0,
            'sync_jobs_deleted': 0,
            'pinecone_vectors_deleted': 0,
            'connection_deleted': True
        }

        if delete_all_data:
            try:
                # 1. Get all ExternalContent for this team
                result = await db.execute(
                    select(ExternalContent).where(
                        and_(
                            ExternalContent.team_id == team_id,
                            ExternalContent.source_type == "github"
                        )
                    )
                )
                external_contents = result.scalars().all()

                # 2. Delete vectors from Pinecone
                if external_contents:
                    try:
                        pinecone_client = Pinecone(api_key=settings.pinecone_api_key)
                        github_index = pinecone_client.Index(settings.pinecone_github_index_name)

                        # Collect all vector IDs
                        vector_ids_code = []
                        vector_ids_semantic = []

                        for content in external_contents:
                            if content.vector_id:
                                vector_ids_code.append(content.vector_id)
                            if content.vector_id_semantic:
                                vector_ids_semantic.append(content.vector_id_semantic)

                        # Delete from Pinecone namespaces
                        code_namespace = f"{team_id}:code"
                        semantic_namespace = f"{team_id}:semantic"

                        if vector_ids_code:
                            github_index.delete(ids=vector_ids_code, namespace=code_namespace)
                            stats['pinecone_vectors_deleted'] += len(vector_ids_code)

                        if vector_ids_semantic:
                            github_index.delete(ids=vector_ids_semantic, namespace=semantic_namespace)
                            stats['pinecone_vectors_deleted'] += len(vector_ids_semantic)

                        logger.info(
                            "pinecone_vectors_deleted",
                            team_id=team_id,
                            code_vectors=len(vector_ids_code),
                            semantic_vectors=len(vector_ids_semantic)
                        )
                    except Exception as e:
                        logger.error("pinecone_deletion_failed", error=str(e))
                        # Continue with database deletion even if Pinecone fails

                # 3. Delete ExternalContent records
                delete_content_stmt = sql_delete(ExternalContent).where(
                    and_(
                        ExternalContent.team_id == team_id,
                        ExternalContent.source_type == "github"
                    )
                )
                result = await db.execute(delete_content_stmt)
                stats['external_content_deleted'] = result.rowcount

                # 4. Delete IntegrationSyncJob records
                delete_jobs_stmt = sql_delete(IntegrationSyncJob).where(
                    and_(
                        IntegrationSyncJob.team_id == team_id,
                        IntegrationSyncJob.integration_type == "github"
                    )
                )
                result = await db.execute(delete_jobs_stmt)
                stats['sync_jobs_deleted'] = result.rowcount

                delete_integrations_stmt = sql_delete(Integration).where(
                    and_(
                        Integration.team_id == team_id,
                        Integration.integration_type == "github"
                    )
                )
                result = await db.execute(delete_integrations_stmt)
                stats['integrations_deleted'] = result.rowcount

                logger.info(
                    "github_data_deleted",
                    team_id=team_id,
                    stats=stats
                )

            except Exception as e:
                await db.rollback()
                logger.error("github_data_deletion_failed", error=str(e))
                raise

        # 5. Delete the connection record itself
        await db.delete(connection)
        await db.commit()

        logger.info(
            "github_integration_fully_disconnected",
            user_id=user_id,
            team_id=team_id,
            connection_id=connection.id,
            stats=stats
        )

        return {
            'success': True,
            'message': 'GitHub integration disconnected and all data deleted',
            'stats': stats
        }

    async def get_or_create_integration(
        self,
        db: AsyncSession,
        team_id: str,
        enabled_by_user_id: str
    ) -> Integration:
        """
        Get or create GitHub integration for workspace

        Args:
            db: Database session
            team_id: Slack workspace team ID
            enabled_by_user_id: User who enabled the integration

        Returns:
            Integration record
        """
        result = await db.execute(
            select(Integration).where(
                and_(
                    Integration.team_id == team_id,
                    Integration.integration_type == "github"
                )
            )
        )
        integration = result.scalar_one_or_none()

        if integration:
            # Reactivate if disabled
            if not integration.is_active:
                integration.is_active = True
                integration.enabled_at = datetime.utcnow()
                integration.disabled_at = None
                integration.updated_at = datetime.utcnow()
                await db.commit()
                await db.refresh(integration)
                logger.info("github_integration_reactivated", team_id=team_id)
            return integration

        # Create new integration
        integration = Integration(
            id=str(uuid.uuid4()),
            team_id=team_id,
            integration_type="github",
            is_active=True,
            enabled_at=datetime.utcnow(),
            enabled_by_user_id=enabled_by_user_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        db.add(integration)
        await db.commit()
        await db.refresh(integration)

        logger.info(
            "github_integration_created",
            team_id=team_id,
            integration_id=integration.id
        )
        return integration


# Global service instance
_github_oauth_service = None


def get_github_oauth_service() -> GitHubOAuthService:
    """Get or create the global GitHub OAuth service instance"""
    global _github_oauth_service

    if _github_oauth_service is None:
        _github_oauth_service = GitHubOAuthService()

    return _github_oauth_service
