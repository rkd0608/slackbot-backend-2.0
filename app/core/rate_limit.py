"""Rate limiting middleware using Redis"""
from datetime import datetime, timedelta

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.cache import cache_manager
from app.core.logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Token bucket rate limiter using Redis"""

    def __init__(self):
        self.enabled = True

    async def check_rate_limit(
        self,
        key: str,
        max_requests: int,
        window_seconds: int
    ) -> dict:
        """
        Check if request is within rate limit using atomic Redis operations

        Args:
            key: Unique identifier (user_id, ip_address, api_key, etc.)
            max_requests: Maximum requests allowed in window
            window_seconds: Time window in seconds

        Returns:
            dict with rate limit info

        Raises:
            HTTPException if rate limit exceeded
        """

        cache_key = f"rate_limit:{key}:{window_seconds}"
        reset_key = f"rate_limit_reset:{key}:{window_seconds}"

        # Atomically increment counter
        count = await cache_manager.incr(cache_key)

        if count == 1:
            # First request in window - set TTL and reset time
            await cache_manager.expire(cache_key, window_seconds)
            reset_at = (datetime.utcnow() + timedelta(seconds=window_seconds)).isoformat()
            await cache_manager.set(reset_key, reset_at, ttl=window_seconds)
        else:
            # Get existing reset time
            reset_at = await cache_manager.get(reset_key)
            if not reset_at:
                # Fallback if reset time is missing
                reset_at = (datetime.utcnow() + timedelta(seconds=window_seconds)).isoformat()
                await cache_manager.set(reset_key, reset_at, ttl=window_seconds)

        if count > max_requests:
            # Rate limit exceeded
            logger.warning(
                "rate_limit_exceeded",
                key=key,
                count=count,
                limit=max_requests,
                window=window_seconds
            )

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {max_requests} requests per {window_seconds} seconds.",
                headers={
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": reset_at,
                    "Retry-After": str(window_seconds)
                }
            )

        return {
            "allowed": True,
            "limit": max_requests,
            "remaining": max_requests - count,
            "reset_at": reset_at
        }

    async def check_workspace_rate_limit(
        self,
        team_id: str,
        endpoint_type: str = "default"
    ) -> dict:
        """
        Check workspace-specific rate limits

        Args:
            team_id: Workspace team ID
            endpoint_type: Type of endpoint (query, admin, etc.)

        Returns:
            Rate limit info
        """

        # Different limits for different endpoint types
        limits = {
            "query": {"max": 100, "window": 3600},  # 100 queries per hour
            "admin": {"max": 1000, "window": 3600},  # 1000 admin requests per hour
            "default": {"max": 500, "window": 3600}  # 500 requests per hour
        }

        config = limits.get(endpoint_type, limits["default"])

        key = f"workspace:{team_id}:{endpoint_type}"
        return await self.check_rate_limit(
            key=key,
            max_requests=config["max"],
            window_seconds=config["window"]
        )

    async def check_ip_rate_limit(
        self,
        ip_address: str,
        max_requests: int = 60,
        window_seconds: int = 60
    ) -> dict:
        """
        Check IP-based rate limit (prevents abuse)

        Default: 60 requests per minute per IP
        """

        key = f"ip:{ip_address}"
        return await self.check_rate_limit(
            key=key,
            max_requests=max_requests,
            window_seconds=window_seconds
        )

    async def check_user_rate_limit(
        self,
        user_id: str,
        max_requests: int = 100,
        window_seconds: int = 60
    ) -> dict:
        """
        Check user-based rate limit

        Default: 100 requests per minute per user
        """

        key = f"user:{user_id}"
        return await self.check_rate_limit(
            key=key,
            max_requests=max_requests,
            window_seconds=window_seconds
        )


# Global rate limiter instance
rate_limiter = RateLimiter()


# Middleware for automatic rate limiting


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware to apply rate limiting to all requests

    Applies IP-based rate limiting to prevent abuse
    """

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path in ["/", "/health", "/api/v1/health"]:
            return await call_next(request)

        # Get client IP
        client_ip = request.client.host if request.client else "unknown"

        # Check IP rate limit (60 req/min per IP)
        try:
            rate_info = await rate_limiter.check_ip_rate_limit(
                ip_address=client_ip,
                max_requests=60,
                window_seconds=60
            )

            # Add rate limit headers to response
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(rate_info["limit"])
            response.headers["X-RateLimit-Remaining"] = str(rate_info["remaining"])
            response.headers["X-RateLimit-Reset"] = rate_info["reset_at"]

            return response

        except HTTPException as e:
            # Rate limit exceeded
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
                headers=e.headers
            )


# Dependency functions for specific rate limiting
async def rate_limit_workspace(request: Request, team_id: str, endpoint_type: str = "default"):
    """
    Dependency to rate limit by workspace

    Usage:
        @router.get("/workspace/{team_id}/data")
        async def get_data(
            team_id: str,
            _: None = Depends(lambda req, tid=team_id: rate_limit_workspace(req, tid, "query"))
        ):
            pass
    """
    return await rate_limiter.check_workspace_rate_limit(team_id, endpoint_type)


async def rate_limit_user(request: Request, user_id: str):
    """
    Dependency to rate limit by user

    Usage:
        @router.get("/user/data")
        async def get_data(
            current_user: User = Depends(get_current_user),
            _: None = Depends(lambda req: rate_limit_user(req, current_user.user_id))
        ):
            pass
    """
    return await rate_limiter.check_user_rate_limit(user_id)
