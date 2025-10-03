"""Main entry point for event consumer worker"""
import asyncio
from app.workers.event_consumer import event_consumer
from app.core.database import db_manager
from app.core.cache import cache_manager
from app.core.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


async def main():
    """Run event consumer"""
    db_manager.initialize()
    await cache_manager.initialize()

    try:
        event_consumer.connect()
        logger.info("event_consumer_ready")
        event_consumer.start_consuming()
    except KeyboardInterrupt:
        logger.info("event_consumer_shutdown")
    finally:
        event_consumer.close()
        await db_manager.close()
        await cache_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
