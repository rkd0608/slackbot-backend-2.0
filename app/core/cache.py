"""Redis cache management"""
import json
from typing import Optional, Any
import redis.asyncio as redis
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CacheManager:
    """Manages Redis cache connections and operations"""

    def __init__(self):
        self.client: Optional[redis.Redis] = None

    async def initialize(self):
        """Initialize Redis connection"""
        self.client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50
        )

        # Test connection
        await self.client.ping()
        logger.info("cache_initialized", redis_host=settings.redis_host)

    async def close(self):
        """Close Redis connection"""
        if self.client:
            await self.client.close()
        logger.info("cache_closed")

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        try:
            value = await self.client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error("cache_get_error", key=key, error=str(e))
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache with optional TTL"""
        try:
            serialized = json.dumps(value)
            if ttl:
                await self.client.setex(key, ttl, serialized)
            else:
                await self.client.set(key, serialized)
            return True
        except Exception as e:
            logger.error("cache_set_error", key=key, error=str(e))
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from cache"""
        try:
            await self.client.delete(key)
            return True
        except Exception as e:
            logger.error("cache_delete_error", key=key, error=str(e))
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache"""
        try:
            return await self.client.exists(key) > 0
        except Exception as e:
            logger.error("cache_exists_error", key=key, error=str(e))
            return False


# Global cache manager instance
cache_manager = CacheManager()
