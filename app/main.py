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

from app.api import health, events, admin, query, answer, commands, interactions, oauth, billing, stripe_webhooks, admin_dashboard, jobs, auth, onboarding, workspace, github_oauth, github_sync

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
        await queue_manager.initialize()
        vector_db_manager.initialize()

        # Storage is optional - don't fail startup if unavailable
        try:
            storage_manager.initialize()
        except Exception as e:
            logger.warning("storage_init_skipped", error=str(e))

        slack_client_manager.initialize()

        # Start metrics updater background task
        from app.services.metrics_updater import start_metrics_updater
        await start_metrics_updater()

        # Initialize and start background job scheduler
        from app.core.scheduler import scheduler_manager
        scheduler_manager.initialize()
        scheduler_manager.start()
        logger.info("scheduler_started")

        # Start RabbitMQ queue consumer
        from app.workers.queue_consumer import queue_consumer
        await queue_consumer.start()
        logger.info("queue_consumer_started")

        logger.info("application_started")
        yield

    finally:
        # Shutdown
        logger.info("application_shutting_down")

        # Stop background workers
        from app.core.scheduler import scheduler_manager
        from app.workers.queue_consumer import queue_consumer

        scheduler_manager.shutdown()
        await queue_consumer.stop()

        # Close core services
        await db_manager.close()
        await cache_manager.close()
        await queue_manager.close()

        logger.info("application_stopped")


# Create FastAPI app
app = FastAPI(
    title="Slack Intelligence API",
    description="Intelligent Slack AI System with RAG and Knowledge Graph",
    version="1.0.0",
    lifespan=lifespan
)

# Middleware (order matters - add from innermost to outermost)
# Rate limiting middleware
from app.core.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# Slack signature verification body cache middleware
from app.core.slack_verification import SlackRequestBodyCacheMiddleware
app.add_middleware(SlackRequestBodyCacheMiddleware)

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
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(onboarding.router, prefix="/api/v1", tags=["onboarding"])
app.include_router(workspace.router, prefix="/api/v1", tags=["workspace"])
app.include_router(events.router, prefix="/api/v1", tags=["events"])
app.include_router(commands.router, prefix="/api/v1", tags=["commands"])
app.include_router(interactions.router, prefix="/api/v1", tags=["interactions"])
# Note: Evaluation router removed - replaced with knowledge graph system
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(query.router, prefix="/api/v1", tags=["query"])
app.include_router(answer.router, prefix="/api/v1", tags=["answer"])
app.include_router(oauth.router, tags=["oauth"])  # OAuth endpoints without /api/v1 prefix
app.include_router(github_oauth.router, tags=["github-oauth"])  # GitHub OAuth endpoints
app.include_router(github_sync.router, tags=["github-sync"])  # GitHub sync/indexing endpoints
app.include_router(billing.router, prefix="/api/v1", tags=["billing"])
app.include_router(stripe_webhooks.router, prefix="/api/v1", tags=["stripe"])
app.include_router(admin_dashboard.router, prefix="/api/v1", tags=["admin-dashboard"])
app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"])


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
