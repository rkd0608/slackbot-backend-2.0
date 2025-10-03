"""RabbitMQ message queue management"""
import json
import time
from typing import Optional, Callable, Any
import pika
from pika.adapters.asyncio_connection import AsyncioConnection
from pika.exchange_type import ExchangeType
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueueManager:
    """Manages RabbitMQ connections and message publishing"""

    # Queue names
    EVENTS_QUEUE = "slack.events"
    EMBEDDINGS_QUEUE = "slack.embeddings"
    PROCESSING_QUEUE = "slack.processing"
    DLQ_QUEUE = "slack.dlq"

    # Exchange names
    EVENTS_EXCHANGE = "slack.events.exchange"

    def __init__(self):
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[pika.channel.Channel] = None

    def initialize(self):
        """Initialize RabbitMQ connection with retry logic"""
        parameters = pika.ConnectionParameters(
            host=settings.rabbitmq_host,
            port=settings.rabbitmq_port,
            virtual_host=settings.rabbitmq_vhost,
            credentials=pika.PlainCredentials(
                settings.rabbitmq_user,
                settings.rabbitmq_password
            ),
            heartbeat=600,
            blocked_connection_timeout=300
        )

        # Retry logic for RabbitMQ connection
        max_retries = 5
        retry_delay = 2  # seconds

        for attempt in range(max_retries):
            try:
                logger.info(f"rabbitmq_connection_attempt", attempt=attempt + 1, host=settings.rabbitmq_host)
                self.connection = pika.BlockingConnection(parameters)
                self.channel = self.connection.channel()
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        "rabbitmq_connection_failed_retrying",
                        attempt=attempt + 1,
                        error=str(e),
                        retry_in=retry_delay
                    )
                    time.sleep(retry_delay)
                else:
                    logger.error("rabbitmq_connection_failed", error=str(e))
                    raise

        # Declare exchanges
        self.channel.exchange_declare(
            exchange=self.EVENTS_EXCHANGE,
            exchange_type=ExchangeType.topic,
            durable=True
        )

        # Declare queues
        self._declare_queue(self.EVENTS_QUEUE)
        self._declare_queue(self.EMBEDDINGS_QUEUE)
        self._declare_queue(self.PROCESSING_QUEUE)
        self._declare_queue(self.DLQ_QUEUE)

        # Bind queues to exchange
        self.channel.queue_bind(
            exchange=self.EVENTS_EXCHANGE,
            queue=self.EVENTS_QUEUE,
            routing_key="slack.event.*"
        )

        logger.info("queue_initialized", rabbitmq_host=settings.rabbitmq_host)

    def _declare_queue(self, queue_name: str):
        """Declare a durable queue"""
        self.channel.queue_declare(
            queue=queue_name,
            durable=True,
            arguments={
                'x-message-ttl': 86400000,  # 24 hours
                'x-max-length': 100000
            }
        )

    def publish(self, queue: str, message: dict, routing_key: Optional[str] = None) -> bool:
        """Publish message to queue"""
        try:
            body = json.dumps(message)

            if routing_key:
                # Publish to exchange with routing key
                self.channel.basic_publish(
                    exchange=self.EVENTS_EXCHANGE,
                    routing_key=routing_key,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # Persistent
                        content_type='application/json'
                    )
                )
            else:
                # Publish directly to queue
                self.channel.basic_publish(
                    exchange='',
                    routing_key=queue,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        content_type='application/json'
                    )
                )

            return True
        except Exception as e:
            logger.error("queue_publish_error", queue=queue, error=str(e))
            return False

    def close(self):
        """Close RabbitMQ connection"""
        if self.connection and not self.connection.is_closed:
            self.connection.close()
        logger.info("queue_closed")


# Global queue manager instance
queue_manager = QueueManager()
