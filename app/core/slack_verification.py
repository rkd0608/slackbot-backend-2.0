"""Slack request signature verification middleware"""
import hmac
import hashlib
import time
from fastapi import Request, HTTPException, status
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SlackSignatureVerifier:
    """Verifies Slack request signatures to ensure requests are from Slack"""

    def __init__(self):
        self.signing_secret = settings.slack_signing_secret.encode()

    def verify_signature(
        self,
        request_body: bytes,
        timestamp: str,
        signature: str
    ) -> bool:
        """
        Verify Slack request signature

        Args:
            request_body: Raw request body bytes
            timestamp: X-Slack-Request-Timestamp header
            signature: X-Slack-Signature header

        Returns:
            True if signature is valid, False otherwise
        """

        # Check timestamp to prevent replay attacks (within 5 minutes)
        try:
            request_timestamp = int(timestamp)
        except (ValueError, TypeError):
            logger.warning("invalid_timestamp", timestamp=timestamp)
            return False

        current_time = int(time.time())
        time_diff = abs(current_time - request_timestamp)

        if time_diff > 60 * 5:  # 5 minutes
            logger.warning(
                "timestamp_too_old",
                timestamp=timestamp,
                current_time=current_time,
                diff_seconds=time_diff
            )
            return False

        # Compute expected signature
        sig_basestring = f"v0:{timestamp}:{request_body.decode('utf-8')}"
        expected_signature = 'v0=' + hmac.new(
            self.signing_secret,
            sig_basestring.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Compare signatures (timing-safe comparison)
        is_valid = hmac.compare_digest(expected_signature, signature)

        if not is_valid:
            logger.warning(
                "signature_mismatch",
                expected=expected_signature[:20] + "...",
                received=signature[:20] + "..."
            )

        return is_valid

    async def verify_request(self, request: Request) -> bool:
        """
        Verify a FastAPI request from Slack

        Args:
            request: FastAPI Request object

        Returns:
            True if verified

        Raises:
            HTTPException if verification fails
        """

        # Get headers
        timestamp = request.headers.get("X-Slack-Request-Timestamp")
        signature = request.headers.get("X-Slack-Signature")

        if not timestamp or not signature:
            logger.error("missing_slack_headers")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Slack signature headers"
            )

        # Get raw body (must be cached during middleware)
        if not hasattr(request.state, "body"):
            logger.error("request_body_not_cached")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Request body not available for verification"
            )

        body = request.state.body

        # Verify signature
        is_valid = self.verify_signature(body, timestamp, signature)

        if not is_valid:
            logger.error("slack_signature_verification_failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Slack signature"
            )

        logger.info("slack_signature_verified")
        return True


# Global verifier instance
slack_verifier = SlackSignatureVerifier()


# Middleware to cache request body for signature verification
from starlette.middleware.base import BaseHTTPMiddleware


class SlackRequestBodyCacheMiddleware(BaseHTTPMiddleware):
    """
    Middleware to cache raw request body for Slack signature verification.

    This must be added before routes that need signature verification,
    since FastAPI consumes the body stream when parsing.
    """

    async def dispatch(self, request: Request, call_next):
        # Only cache body for Slack webhook endpoints
        if request.url.path.startswith("/api/v1/slack/"):
            # Read and cache the body
            body = await request.body()

            # Store in request state for later verification
            request.state.body = body

            # Create a new receive function that returns the cached body
            # Need to track whether body was already sent to handle multiple receive() calls
            body_sent = False
            original_receive = request._receive

            async def receive():
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {"type": "http.request", "body": body}
                else:
                    # After body is sent, delegate to original receive for disconnect messages
                    return await original_receive()

            # Replace the receive function
            request._receive = receive

        response = await call_next(request)
        return response


# Dependency for routes that require Slack signature verification
async def verify_slack_signature(request: Request) -> bool:
    """
    Dependency that verifies Slack signature

    Usage:
        @router.post("/slack/events")
        async def slack_events(
            verified: bool = Depends(verify_slack_signature)
        ):
            # Request is verified
            pass
    """
    return await slack_verifier.verify_request(request)
