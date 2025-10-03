"""Main entry point for processing consumer worker"""
import asyncio
from app.workers.processing_consumer import processing_consumer
from app.core.database import db_manager
from app.core.cache import cache_manager
from app.services.slack_client import slack_client_manager
from app.core.storage import storage_manager
from app.core.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


async def main():
    """Run processing consumer"""
    db_manager.initialize()
    await cache_manager.initialize()
    storage_manager.initialize()
    slack_client_manager.initialize()

    try:
        processing_consumer.connect()
        logger.info("processing_consumer_ready")
        processing_consumer.start_consuming()
    except KeyboardInterrupt:
        logger.info("processing_consumer_shutdown")
    finally:
        processing_consumer.close()
        await db_manager.close()
        await cache_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
