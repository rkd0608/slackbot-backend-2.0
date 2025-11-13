"""Slack OAuth service for workspace installation"""
import secrets
import httpx
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.workspace import Workspace, InstallationLog
from app.core.config import settings
from app.core.logging import get_logger
from app.core.cache import cache_manager

logger = get_logger(__name__)

# Import workspace_service at module level to avoid circular imports
# This will be used after workspace token updates to invalidate cache
_workspace_service = None

def _get_workspace_service():
    """Lazy import to avoid circular dependency"""
    global _workspace_service
    if _workspace_service is None:
        from app.services.workspace_service import workspace_service
        _workspace_service = workspace_service
    return _workspace_service


class OAuthService:
    """Handles Slack OAuth flow for workspace installation"""

    def __init__(self):
        self.client_id = settings.slack_client_id
        self.client_secret = settings.slack_client_secret
        self.redirect_uri = settings.oauth_redirect_uri

        # User scopes (12 scopes - matches Glean)
        self.user_scopes = [
            "channels:read",
            "groups:read",
            "im:read",
            "mpim:read",
            "search:read.files",
            "search:read.im",
            "search:read.mpim",
            "search:read.private",
            "search:read.public",
            "team:read",
            "users:read",
            "users:read.email"
        ]

        # Bot token scopes (23 scopes - complete set for all features)
        self.bot_scopes = [
            "app_mentions:read",      # Read when bot is mentioned
            "assistant:write",         # Use Assistant API
            "channels:history",        # Read public channel messages
            "channels:join",          # Join channels automatically
            "channels:read",          # View basic channel info
            "chat:write",             # Send messages
            "chat:write.public",      # Send to channels bot isn't in
            "commands",               # Receive slash commands
            "files:read",             # Read file info and download files
            "groups:history",         # Read private channel messages
            "groups:read",            # View private channel info
            "im:history",             # Read DM messages
            "im:read",                # View DM info
            "im:write",               # Send DMs
            "links:read",             # Read message links
            "links:write",            # Unfurl links
            "mpim:history",           # Read group DM messages
            "mpim:read",              # View group DM info
            "reactions:read",         # Read reactions for feedback
            "reactions:write",        # Add reactions
            "team:read",              # Read workspace info
            "users:read",             # Read user profiles
            "users:read.email"        # Read user emails
        ]

    async def generate_oauth_url(self) -> Dict[str, str]:
        """Generate Slack OAuth URL with state token"""

        # Generate CSRF state token
        state_token = secrets.token_urlsafe(32)

        # Store state token in Redis with 5 minute TTL
        await cache_manager.set(
            f"oauth:state:{state_token}",
            {"created_at": datetime.utcnow().isoformat()},
            ttl=300  # 5 minutes
        )

        # Build OAuth URL with both user and bot scopes
        user_scopes_string = ",".join(self.user_scopes)
        bot_scopes_string = ",".join(self.bot_scopes)
        oauth_url = (
            f"https://slack.com/oauth/v2/authorize?"
            f"client_id={self.client_id}&"
            f"scope={bot_scopes_string}&"
            f"user_scope={user_scopes_string}&"
            f"redirect_uri={self.redirect_uri}&"
            f"state={state_token}"
        )

        logger.info("oauth_url_generated", bot_scopes=len(self.bot_scopes), user_scopes=len(self.user_scopes))

        return {
            "oauth_url": oauth_url,
            "state": state_token
        }

    async def verify_state_token(self, state: str) -> bool:
        """Verify OAuth state token to prevent CSRF"""

        cached_state = await cache_manager.get(f"oauth:state:{state}")

        if not cached_state:
            logger.warning("oauth_state_invalid")
            return False

        # Delete token after verification (one-time use)
        await cache_manager.delete(f"oauth:state:{state}")

        return True

    async def exchange_code_for_token(
        self,
        code: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Exchange authorization code for access tokens"""

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://slack.com/api/oauth.v2.access",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "code": code,
                        "redirect_uri": self.redirect_uri
                    }
                )

                result = response.json()

                if not result.get("ok"):
                    error = result.get("error", "unknown_error")
                    logger.error("oauth_token_exchange_failed", error=error)
                    raise Exception(f"Token exchange failed: {error}")

                logger.info("oauth_token_exchange_success", team_id=result.get("team", {}).get("id"))

                return result

        except Exception as e:
            logger.error("oauth_token_exchange_error", error=str(e))
            raise

    async def get_workspace_info(self, access_token: str) -> Dict[str, Any]:
        """Get workspace information from Slack"""

        try:
            async with httpx.AsyncClient() as client:
                # Get bot info (includes team_id, team name, etc.)
                auth_response = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                auth_data = auth_response.json()

                # Build team info from auth.test (no need for team.info which requires team:read scope)
                team_info = {
                    "id": auth_data.get("team_id"),
                    "name": auth_data.get("team"),
                    "url": auth_data.get("url"),
                    # Note: domain is not in auth.test, will be None
                    "domain": None
                }

                # Count users for tier detection
                users_response = await client.post(
                    "https://slack.com/api/users.list",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                users_data = users_response.json()

                # Count active, non-bot users
                active_users = [
                    u for u in users_data.get("members", [])
                    if not u.get("is_bot") and not u.get("deleted")
                ]
                user_count = len(active_users)

                return {
                    "team": team_info,
                    "auth": auth_data,
                    "user_count": user_count
                }

        except Exception as e:
            logger.error("get_workspace_info_error", error=str(e))
            raise

    def determine_pricing_tier(self, user_count: int) -> str:
        """Determine pricing tier based on user count"""

        if user_count <= 50:
            return "starter"
        elif user_count <= 250:
            return "growth"
        elif user_count <= 1000:
            return "business"
        else:
            return "enterprise"

    def get_query_limit(self, tier: str) -> int:
        """Get monthly query limit for tier"""

        limits = {
            "starter": 500,
            "growth": 2000,
            "business": 10000,
            "enterprise": 50000
        }
        return limits.get(tier, 500)

    async def validate_token(self, token: str) -> bool:
        """Validate that a Slack token is currently active"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {token}"}
                )
                result = response.json()

                if result.get("ok"):
                    logger.info(
                        "token_validated",
                        team_id=result.get("team_id"),
                        bot_id=result.get("bot_id")
                    )
                    return True
                else:
                    logger.error(
                        "token_invalid",
                        error=result.get("error"),
                        token_prefix=token[:20] if token else "none"
                    )
                    return False
        except Exception as e:
            logger.error("token_validation_error", error=str(e))
            return False

    async def create_workspace(
        self,
        oauth_data: Dict[str, Any],
        workspace_info: Dict[str, Any],
        installer_email: Optional[str],
        db: AsyncSession
    ) -> Workspace:
        """Create workspace record from OAuth data"""

        team = workspace_info["team"]
        auth = workspace_info["auth"]
        user_count = workspace_info["user_count"]

        # Validate token before saving (catches immediate issues)
        bot_token = oauth_data["access_token"]
        is_valid = await self.validate_token(bot_token)
        if not is_valid:
            logger.warning(
                "saving_potentially_invalid_token",
                team_id=team["id"],
                message="Token failed validation but saving anyway - may need manual update"
            )

        # Determine tier and limits
        tier = self.determine_pricing_tier(user_count)
        query_limit = self.get_query_limit(tier)

        # Check if workspace already exists (reinstallation)
        stmt = select(Workspace).where(Workspace.team_id == team["id"])
        result = await db.execute(stmt)
        existing_workspace = result.scalar_one_or_none()

        if existing_workspace:
            # Reinstallation - update tokens and reactivate
            existing_workspace.bot_access_token = oauth_data["access_token"]
            existing_workspace.bot_user_id = auth["user_id"]
            existing_workspace.bot_scopes = oauth_data.get("scope", "").split(",")
            existing_workspace.is_active = 1
            existing_workspace.updated_at = datetime.utcnow()

            # Get user ID with fallback
            authed_user = oauth_data.get("authed_user", {})
            user_id = authed_user.get("id") or authed_user.get("user_id") or existing_workspace.installer_user_id

            # Store installer's user token for Glean-style indexing
            if authed_user.get("access_token"):
                try:
                    from app.core.encryption import get_encryption_service
                    encryption_service = get_encryption_service()
                    existing_workspace.installer_user_token = encryption_service.encrypt(
                        authed_user.get("access_token")
                    )
                    logger.info(
                        "installer_user_token_updated",
                        team_id=team["id"],
                        user_id=user_id
                    )
                except Exception as e:
                    logger.error(
                        "installer_token_encryption_failed",
                        error=str(e),
                        team_id=team["id"]
                    )

            # Log reinstallation
            log = InstallationLog(
                team_id=team["id"],
                event_type="reinstalled",
                event_data={
                    "previous_status": existing_workspace.subscription_status,
                    "scopes": existing_workspace.bot_scopes
                },
                user_id=user_id,
                created_at=datetime.utcnow()
            )
            db.add(log)

            await db.commit()
            await db.refresh(existing_workspace)

            # Invalidate workspace cache since token was updated
            ws_service = _get_workspace_service()
            await ws_service.invalidate_workspace_cache(team["id"])

            logger.info(
                "workspace_reinstalled",
                team_id=team["id"],
                team_name=team["name"]
            )

            return existing_workspace

        # New installation
        trial_start = datetime.utcnow()
        trial_end = trial_start + timedelta(days=14)

        # Get installer user ID with fallback
        authed_user = oauth_data.get("authed_user", {})
        installer_user_id = authed_user.get("id") or authed_user.get("user_id") or auth.get("user_id", "")

        # Encrypt installer's user token for Glean-style indexing
        installer_user_token_encrypted = None
        if authed_user.get("access_token"):
            try:
                from app.core.encryption import get_encryption_service
                encryption_service = get_encryption_service()
                installer_user_token_encrypted = encryption_service.encrypt(
                    authed_user.get("access_token")
                )
                logger.info(
                    "installer_user_token_encrypted",
                    team_id=team["id"],
                    installer_user_id=installer_user_id
                )
            except Exception as e:
                logger.error(
                    "installer_token_encryption_failed_new_install",
                    error=str(e),
                    team_id=team["id"]
                )

        workspace = Workspace(
            team_id=team["id"],
            team_name=team["name"],
            team_domain=team.get("domain"),
            team_url=team.get("url"),
            bot_access_token=oauth_data["access_token"],
            bot_user_id=auth["user_id"],
            bot_scopes=oauth_data.get("scope", "").split(","),
            installer_user_id=installer_user_id,
            installer_user_token=installer_user_token_encrypted,
            installer_email=installer_email,
            installed_at=datetime.utcnow(),
            is_active=1,
            subscription_status="trial",
            subscription_tier=tier,
            trial_started_at=trial_start,
            trial_ends_at=trial_end,
            user_count=user_count,
            active_user_count=user_count,
            monthly_query_limit=query_limit,
            queries_used_this_month=0,
            indexing_status="pending",
            created_at=datetime.utcnow()
        )

        db.add(workspace)

        # Log installation
        log = InstallationLog(
            team_id=team["id"],
            event_type="installed",
            event_data={
                "team_name": team["name"],
                "user_count": user_count,
                "tier": tier,
                "scopes": workspace.bot_scopes
            },
            user_id=workspace.installer_user_id,
            user_email=installer_email,
            created_at=datetime.utcnow()
        )
        db.add(log)

        await db.commit()
        await db.refresh(workspace)

        logger.info(
            "workspace_created",
            team_id=team["id"],
            team_name=team["name"],
            tier=tier,
            user_count=user_count
        )

        # NOTE: Initial indexing is NOT triggered here automatically
        # It will be triggered after user completes onboarding and configures:
        # - Which channels to index
        # - Date range preferences
        # - Other indexing settings
        # The indexing will be triggered from the onboarding completion endpoint

        return workspace


# Global OAuth service instance
oauth_service = OAuthService()
