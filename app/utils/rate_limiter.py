"""Rate limiting using Redis"""
import time
from typing import Optional
from app.core.cache import cache_manager
from app.core.config import settings
from app.core.exceptions import RateLimitException
from app.core.logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Token bucket rate limiter using Redis"""

    def __init__(self):
        self.hourly_limit = settings.query_rate_limit_per_hour
        self.burst_limit = settings.query_burst_limit_per_minute

    async def check_rate_limit(self, user_id: str) -> bool:
        """Check if user has exceeded rate limit"""

        # Check hourly limit
        hourly_key = f"rate_limit:hourly:{user_id}:{int(time.time() // 3600)}"
        hourly_count = await cache_manager.client.get(hourly_key)

        if hourly_count and int(hourly_count) >= self.hourly_limit:
            logger.warning("hourly_rate_limit_exceeded", user_id=user_id)
            raise RateLimitException(f"Hourly rate limit exceeded: {self.hourly_limit} queries/hour")

        # Check burst limit (per minute)
        burst_key = f"rate_limit:burst:{user_id}:{int(time.time() // 60)}"
        burst_count = await cache_manager.client.get(burst_key)

        if burst_count and int(burst_count) >= self.burst_limit:
            logger.warning("burst_rate_limit_exceeded", user_id=user_id)
            raise RateLimitException(f"Burst rate limit exceeded: {self.burst_limit} queries/minute")

        # Increment counters
        await cache_manager.client.incr(hourly_key)
        await cache_manager.client.expire(hourly_key, 3600)

        await cache_manager.client.incr(burst_key)
        await cache_manager.client.expire(burst_key, 60)

        return True


# Global rate limiter instance
rate_limiter = RateLimiter()
