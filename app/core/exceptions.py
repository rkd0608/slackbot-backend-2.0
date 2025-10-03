"""Custom exceptions for the application"""


class SlackIntelligenceException(Exception):
    """Base exception for the application"""
    pass


class DatabaseException(SlackIntelligenceException):
    """Database-related errors"""
    pass


class VectorDBException(SlackIntelligenceException):
    """Vector database errors"""
    pass


class SlackAPIException(SlackIntelligenceException):
    """Slack API errors"""
    pass


class EmbeddingException(SlackIntelligenceException):
    """Embedding generation errors"""
    pass


class QueueException(SlackIntelligenceException):
    """Message queue errors"""
    pass


class StorageException(SlackIntelligenceException):
    """S3 storage errors"""
    pass


class PermissionException(SlackIntelligenceException):
    """Permission and access control errors"""
    pass


class RateLimitException(SlackIntelligenceException):
    """Rate limiting errors"""
    pass


class ValidationException(SlackIntelligenceException):
    """Input validation errors"""
    pass
