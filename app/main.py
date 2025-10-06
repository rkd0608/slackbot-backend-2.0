"""Main FastAPI application with lifecycle management"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.database import db_manager
from app.core.cache import cache_manager
from app.core.queue import queue_manager
from app.core.vector_db import vector_db_manager
from app.core.storage import storage_manager
from app.services.slack_client import slack_client_manager
from app.core.monitoring import registry
from app.core.exceptions import (
    SlackIntelligenceException,
    RateLimitException,
    PermissionException
)

from app.api import health, events, admin, query, answer, commands, interactions, evaluation

# Setup logging
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # Startup
    logger.info("application_starting", environment=settings.environment)

    try:
        # Initialize core services
        db_manager.initialize()
        await cache_manager.initialize()
        queue_manager.initialize()
        vector_db_manager.initialize()
        storage_manager.initialize()
        slack_client_manager.initialize()

        # Start metrics updater background task
        from app.services.metrics_updater import start_metrics_updater
        await start_metrics_updater()

        logger.info("application_started")
        yield

    finally:
        # Shutdown
        logger.info("application_shutting_down")
        await db_manager.close()
        await cache_manager.close()
        queue_manager.close()
        logger.info("application_stopped")


# Create FastAPI app
app = FastAPI(
    title="Slack Intelligence API",
    description="Intelligent Slack AI System with RAG and Knowledge Graph",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.environment == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handlers
@app.exception_handler(RateLimitException)
async def rate_limit_exception_handler(request: Request, exc: RateLimitException):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"error": "rate_limit_exceeded", "message": str(exc)}
    )


@app.exception_handler(PermissionException)
async def permission_exception_handler(request: Request, exc: PermissionException):
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"error": "permission_denied", "message": str(exc)}
    )


@app.exception_handler(SlackIntelligenceException)
async def base_exception_handler(request: Request, exc: SlackIntelligenceException):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "internal_error", "message": str(exc)}
    )


# Include routers
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(events.router, prefix="/api/v1", tags=["events"])
app.include_router(commands.router, prefix="/api/v1", tags=["commands"])
app.include_router(interactions.router, prefix="/api/v1", tags=["interactions"])
app.include_router(evaluation.router, prefix="/api/v1", tags=["evaluation"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(query.router, prefix="/api/v1", tags=["query"])
app.include_router(answer.router, prefix="/api/v1", tags=["answer"])


# Metrics endpoint
@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(
        content=generate_latest(registry),
        media_type=CONTENT_TYPE_LATEST
    )


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "service": "Slack Intelligence API",
        "version": "1.0.0",
        "environment": settings.environment,
        "documentation": "/docs"
    }
