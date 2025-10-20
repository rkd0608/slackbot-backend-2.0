"""Input validation middleware and utilities"""
import re
from typing import Any, Optional
from pydantic import BaseModel, Field, validator
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueryValidator(BaseModel):
    """Validates user query inputs"""

    query: str = Field(..., min_length=1, max_length=5000)

    @validator('query')
    def sanitize_query(cls, v):
        """Sanitize query input"""
        # Remove null bytes
        v = v.replace('\x00', '')

        # Strip excessive whitespace
        v = ' '.join(v.split())

        # Check for SQL injection patterns
        sql_patterns = [
            r"(\bUNION\b.*\bSELECT\b)",
            r"(\bDROP\b.*\bTABLE\b)",
            r"(\bINSERT\b.*\bINTO\b)",
            r"(\bUPDATE\b.*\bSET\b)",
            r"(\bDELETE\b.*\bFROM\b)",
        ]

        for pattern in sql_patterns:
            if re.search(pattern, v, re.IGNORECASE):
                logger.warning("potential_sql_injection_detected", pattern=pattern)
                raise ValueError("Invalid query format")

        # Check for XSS patterns
        xss_patterns = [
            r"<script[^>]*>.*?</script>",
            r"javascript:",
            r"on\w+\s*=",
        ]

        for pattern in xss_patterns:
            if re.search(pattern, v, re.IGNORECASE):
                logger.warning("potential_xss_detected", pattern=pattern)
                raise ValueError("Invalid query format")

        return v


class TeamIdValidator(BaseModel):
    """Validates Slack team/workspace IDs"""

    team_id: str = Field(..., min_length=1, max_length=20)

    @validator('team_id')
    def validate_team_id(cls, v):
        """Validate team ID format"""
        # Slack team IDs start with T
        if not re.match(r'^T[A-Z0-9]{8,}$', v):
            logger.warning("invalid_team_id_format", team_id=v[:5])
            raise ValueError("Invalid team ID format")
        return v


class UserIdValidator(BaseModel):
    """Validates Slack user IDs"""

    user_id: str = Field(..., min_length=1, max_length=20)

    @validator('user_id')
    def validate_user_id(cls, v):
        """Validate user ID format"""
        # Slack user IDs start with U or W
        if not re.match(r'^[UW][A-Z0-9]{8,}$', v):
            logger.warning("invalid_user_id_format", user_id=v[:5])
            raise ValueError("Invalid user ID format")
        return v


class ChannelIdValidator(BaseModel):
    """Validates Slack channel IDs"""

    channel_id: str = Field(..., min_length=1, max_length=20)

    @validator('channel_id')
    def validate_channel_id(cls, v):
        """Validate channel ID format"""
        # Slack channel IDs start with C, D, or G
        if not re.match(r'^[CDG][A-Z0-9]{8,}$', v):
            logger.warning("invalid_channel_id_format", channel_id=v[:5])
            raise ValueError("Invalid channel ID format")
        return v


def validate_slack_timestamp(ts: str) -> bool:
    """Validate Slack timestamp format"""
    # Slack timestamps are Unix timestamps with microseconds: 1234567890.123456
    if not re.match(r'^\d{10}\.\d{6}$', ts):
        logger.warning("invalid_timestamp_format", timestamp=ts)
        return False
    return True


def sanitize_file_path(path: str) -> str:
    """Sanitize file paths to prevent path traversal"""
    # Remove path traversal patterns
    path = path.replace('..', '')
    path = path.replace('//', '/')

    # Remove null bytes
    path = path.replace('\x00', '')

    # Only allow alphanumeric, hyphens, underscores, dots, and forward slashes
    if not re.match(r'^[a-zA-Z0-9/_.\-]+$', path):
        logger.warning("invalid_file_path_characters", path=path[:50])
        raise ValueError("Invalid file path")

    return path


def validate_email(email: str) -> bool:
    """Validate email format"""
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        logger.warning("invalid_email_format")
        return False
    return True


def sanitize_html(text: str) -> str:
    """Remove HTML tags from text"""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    return text
