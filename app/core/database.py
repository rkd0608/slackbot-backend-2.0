"""Database connection management"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy import create_engine, pool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# SQLAlchemy Base for ORM models
Base = declarative_base()


class DatabaseManager:
    """Manages database connections and sessions"""

    def __init__(self):
        self.engine = None
        self.async_engine = None
        self.SessionLocal = None
        self.AsyncSessionLocal = None

    def initialize(self):
        """Initialize database engines and session factories"""
        # Synchronous engine for migrations
        self.engine = create_engine(
            settings.mysql_url,
            poolclass=pool.QueuePool,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            echo=settings.environment == "development"
        )

        # Async engine for application: swap the sync driver (pymysql) to async (aiomysql)
        async_url = settings.mysql_url.replace("+pymysql", "+aiomysql")
        self.async_engine = create_async_engine(
            async_url,
            poolclass=pool.AsyncAdaptedQueuePool,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            echo=settings.environment == "development"
        )

        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

        self.AsyncSessionLocal = async_sessionmaker(
            self.async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False
        )

        logger.info("database_initialized", mysql_host=settings.mysql_host)

    async def close(self):
        """Close database connections"""
        if self.async_engine:
            await self.async_engine.dispose()
        if self.engine:
            self.engine.dispose()
        logger.info("database_closed")

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get async database session"""
        async with self.AsyncSessionLocal() as session:
            try:
                yield session
            finally:
                await session.close()


# Global database manager instance
db_manager = DatabaseManager()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI routes"""
    async for session in db_manager.get_session():
        yield session
