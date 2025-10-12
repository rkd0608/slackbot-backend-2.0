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


class OAuthService:
    """Handles Slack OAuth flow for workspace installation"""

    def __init__(self):
        self.client_id = settings.slack_client_id
        self.client_secret = settings.slack_client_secret
        self.redirect_uri = settings.oauth_redirect_uri
        self.scopes = [
            "app_mentions:read",
            "channels:history",
            "channels:read",
            "chat:write",
            "commands",
            "groups:history",
            "groups:read",
            "im:history",
            "im:read",
            "team:read",
            "users:read",
            "users:read.email"
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

        # Build OAuth URL
        scopes_string = ",".join(self.scopes)
        oauth_url = (
            f"https://slack.com/oauth/v2/authorize?"
            f"client_id={self.client_id}&"
            f"scope={scopes_string}&"
            f"redirect_uri={self.redirect_uri}&"
            f"state={state_token}"
        )

        logger.info("oauth_url_generated", state_token=state_token[:10])

        return {
            "oauth_url": oauth_url,
            "state": state_token
        }

    async def verify_state_token(self, state: str) -> bool:
        """Verify OAuth state token to prevent CSRF"""

        cached_state = await cache_manager.get(f"oauth:state:{state}")

        if not cached_state:
            logger.warning("oauth_state_invalid", state=state[:10])
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
                # Get team info
                team_response = await client.post(
                    "https://slack.com/api/team.info",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                team_data = team_response.json()

                # Get bot info
                auth_response = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                auth_data = auth_response.json()

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
                    "team": team_data.get("team", {}),
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

            # Log reinstallation
            log = InstallationLog(
                team_id=team["id"],
                event_type="reinstalled",
                event_data={
                    "previous_status": existing_workspace.subscription_status,
                    "scopes": existing_workspace.bot_scopes
                },
                user_id=oauth_data.get("authed_user", {}).get("id"),
                created_at=datetime.utcnow()
            )
            db.add(log)

            await db.commit()
            await db.refresh(existing_workspace)

            logger.info(
                "workspace_reinstalled",
                team_id=team["id"],
                team_name=team["name"]
            )

            return existing_workspace

        # New installation
        trial_start = datetime.utcnow()
        trial_end = trial_start + timedelta(days=14)

        workspace = Workspace(
            team_id=team["id"],
            team_name=team["name"],
            team_domain=team.get("domain"),
            team_url=team.get("url"),
            bot_access_token=oauth_data["access_token"],
            bot_user_id=auth["user_id"],
            bot_scopes=oauth_data.get("scope", "").split(","),
            installer_user_id=oauth_data.get("authed_user", {}).get("id", ""),
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

        # Trigger initial indexing job
        from app.services.installation_handler import installation_handler
        await installation_handler.trigger_initial_indexing(team["id"], db)

        return workspace


# Global OAuth service instance
oauth_service = OAuthService()
