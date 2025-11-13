"""Structured logging configuration"""
import logging
import sys
from pathlib import Path
from typing import Any
import structlog
from logging.handlers import RotatingFileHandler
from app.core.config import settings


def setup_logging() -> None:
    """Configure structured logging with structlog"""

    # Create logs directory if it doesn't exist
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    # Configure file handler with rotation
    log_file = logs_dir / "app.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=50 * 1024 * 1024,  # 50MB per file
        backupCount=10,  # Keep 10 backup files
        encoding='utf-8'
    )
    file_handler.setLevel(logging.getLevelName(settings.log_level))

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Configure standard logging to use both stdout and file
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.getLevelName(settings.log_level))

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Add console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.getLevelName(settings.log_level))
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(console_handler)

    # Add file handler
    root_logger.addHandler(file_handler)


def get_logger(name: str) -> Any:
    """Get a structured logger instance"""
    return structlog.get_logger(name)
