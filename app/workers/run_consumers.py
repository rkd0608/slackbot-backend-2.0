"""Script to run all consumer workers"""
import asyncio
import signal
import sys
from multiprocessing import Process
from app.workers.event_main import main as event_main
from app.workers.embedding_consumer import embedding_consumer, main as embedding_main
from app.workers.processing_consumer import processing_consumer
from app.core.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


def run_event_consumer():
    """Run event consumer in separate process"""
    try:
        logger.info("starting_event_consumer")
        asyncio.run(event_main())
    except KeyboardInterrupt:
        logger.info("event_consumer_stopped")
    except Exception as e:
        logger.error("event_consumer_error", error=str(e))
        sys.exit(1)


def run_embedding_consumer():
    """Run embedding consumer in separate process"""
    try:
        logger.info("starting_embedding_consumer")
        asyncio.run(embedding_main())
    except KeyboardInterrupt:
        logger.info("embedding_consumer_stopped")
    except Exception as e:
        logger.error("embedding_consumer_error", error=str(e))
        sys.exit(1)


def run_processing_consumer():
    """Run processing consumer in separate process"""
    from app.core.database import db_manager
    from app.core.cache import cache_manager
    from app.core.queue import queue_manager
    from app.core.vector_db import vector_db_manager
    from app.services.slack_client import slack_client_manager
    from app.core.storage import storage_manager

    async def main():
        db_manager.initialize()
        await cache_manager.initialize()
        await queue_manager.initialize()  # Initialize queue_manager for publishing
        vector_db_manager.initialize()  # Initialize Pinecone for embeddings
        slack_client_manager.initialize()  # Initialize Slack client for file downloads
        storage_manager.initialize()  # Initialize S3 for file storage

        try:
            await processing_consumer.connect()
            await processing_consumer.start_consuming()
        finally:
            await processing_consumer.close()
            await queue_manager.close()
            await db_manager.close()
            await cache_manager.close()

    try:
        logger.info("starting_processing_consumer")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("processing_consumer_stopped")
    except Exception as e:
        logger.error("processing_consumer_error", error=str(e))
        sys.exit(1)


def main():
    """Run all consumers as separate processes"""
    logger.info("starting_all_consumers")

    # Create processes
    processes = [
        Process(target=run_event_consumer, name="EventConsumer"),
        Process(target=run_embedding_consumer, name="EmbeddingConsumer"),
        Process(target=run_processing_consumer, name="ProcessingConsumer")
    ]

    # Start all processes
    for p in processes:
        p.start()
        logger.info("process_started", name=p.name, pid=p.pid)

    # Handle shutdown
    def signal_handler(signum, frame):
        logger.info("shutdown_signal_received")
        for p in processes:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Wait for processes
    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
        for p in processes:
            p.terminate()


if __name__ == "__main__":
    main()
