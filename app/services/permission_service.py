"""Permission and access control service"""
import httpx
from typing import List, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.logging import get_logger
from app.core.cache import cache_manager
from app.services.workspace_service import workspace_service

logger = get_logger(__name__)


class PermissionService:
    """Manages user permissions and channel access control"""

    async def get_user_accessible_channels(
        self,
        team_id: str,
        user_id: str,
        db: AsyncSession
    ) -> Set[str]:
        """Get list of channel IDs user has access to"""

        # Check cache first (15 minute TTL)
        cache_key = f"permissions:{team_id}:{user_id}"
        cached = await cache_manager.get(cache_key)

        if cached and isinstance(cached, list):
            logger.info(
                "permissions_cache_hit",
                team_id=team_id,
                user_id=user_id,
                channels=len(cached)
            )
            return set(cached)

        # Cache miss - fetch from Slack API
        try:
            access_token = await workspace_service.get_workspace_token(team_id, db)

            if not access_token:
                logger.error("workspace_token_not_found", team_id=team_id)
                return set()

            async with httpx.AsyncClient() as client:
                # Get user's conversations (channels they're a member of)
                response = await client.post(
                    "https://slack.com/api/users.conversations",
                    headers={"Authorization": f"Bearer {access_token}"},
                    data={
                        "user": user_id,
                        "types": "public_channel,private_channel",
                        "exclude_archived": False,
                        "limit": 1000
                    }
                )

                data = response.json()

                if not data.get("ok"):
                    logger.error(
                        "slack_api_error",
                        error=data.get("error"),
                        team_id=team_id,
                        user_id=user_id
                    )
                    return set()

                channels = data.get("channels", [])
                channel_ids = {ch["id"] for ch in channels}

                logger.info(
                    "permissions_fetched",
                    team_id=team_id,
                    user_id=user_id,
                    channels=len(channel_ids)
                )

                # Cache for 15 minutes
                await cache_manager.set(
                    cache_key,
                    list(channel_ids),
                    ttl=900  # 15 minutes
                )

                return channel_ids

        except Exception as e:
            logger.error(
                "get_accessible_channels_error",
                error=str(e),
                team_id=team_id,
                user_id=user_id
            )
            return set()

    async def filter_results_by_permissions(
        self,
        team_id: str,
        user_id: str,
        results: List[dict],
        db: AsyncSession
    ) -> List[dict]:
        """Filter search results to only include channels user has access to"""

        # Get user's accessible channels
        accessible_channels = await self.get_user_accessible_channels(
            team_id,
            user_id,
            db
        )

        if not accessible_channels:
            logger.warning(
                "no_accessible_channels",
                team_id=team_id,
                user_id=user_id
            )
            return []

        # Filter results
        filtered = []
        for result in results:
            channel_id = result.get("channel_id") or result.get("metadata", {}).get("channel_id")

            if channel_id in accessible_channels:
                filtered.append(result)

        logger.info(
            "results_filtered_by_permissions",
            team_id=team_id,
            user_id=user_id,
            original_count=len(results),
            filtered_count=len(filtered)
        )

        return filtered

    async def user_can_access_channel(
        self,
        team_id: str,
        user_id: str,
        channel_id: str,
        db: AsyncSession
    ) -> bool:
        """Check if user has access to specific channel"""

        accessible_channels = await self.get_user_accessible_channels(
            team_id,
            user_id,
            db
        )

        return channel_id in accessible_channels

    async def invalidate_user_permissions(
        self,
        team_id: str,
        user_id: str
    ) -> None:
        """Invalidate cached permissions for user"""

        cache_key = f"permissions:{team_id}:{user_id}"
        await cache_manager.delete(cache_key)

        logger.info(
            "permissions_cache_invalidated",
            team_id=team_id,
            user_id=user_id
        )

    async def build_channel_filter(
        self,
        team_id: str,
        user_id: str,
        db: AsyncSession
    ) -> Optional[dict]:
        """Build Pinecone metadata filter for user's accessible channels"""

        accessible_channels = await self.get_user_accessible_channels(
            team_id,
            user_id,
            db
        )

        if not accessible_channels:
            return None

        # Build filter for Pinecone query
        return {
            "team_id": team_id,
            "channel_id": {"$in": list(accessible_channels)}
        }


# Global permission service instance
permission_service = PermissionService()
