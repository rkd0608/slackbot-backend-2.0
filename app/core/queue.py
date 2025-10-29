"""RabbitMQ message queue management with aio-pika"""
import json
from typing import Optional
import aio_pika
from aio_pika import ExchangeType
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueueManager:
    """Manages async RabbitMQ connections and message publishing"""

    # Queue names
    EVENTS_QUEUE = "slack.events"
    EMBEDDINGS_QUEUE = "slack.embeddings"
    PROCESSING_QUEUE = "slack.processing"
    DLQ_QUEUE = "slack.dlq"

    # Exchange names
    EVENTS_EXCHANGE = "slack.events.exchange"

    def __init__(self):
        self.connection: Optional[aio_pika.RobustConnection] = None
        self.channel: Optional[aio_pika.Channel] = None
        self.exchange: Optional[aio_pika.Exchange] = None
        self._initialized = False

    async def initialize(self):
        """Initialize async RabbitMQ connection with retry logic"""
        if self._initialized:
            return

        # Build connection URL
        rabbitmq_url = (
            f"amqp://{settings.rabbitmq_user}:{settings.rabbitmq_password}"
            f"@{settings.rabbitmq_host}:{settings.rabbitmq_port}/{settings.rabbitmq_vhost}"
        )

        try:
            logger.info("rabbitmq_connection_attempt", host=settings.rabbitmq_host)

            # Create robust connection (auto-reconnect on failure)
            self.connection = await aio_pika.connect_robust(
                rabbitmq_url,
                heartbeat=600,
                timeout=30
            )

            self.channel = await self.connection.channel()

            # Set QoS - prefetch 10 messages at a time
            await self.channel.set_qos(prefetch_count=10)

            # Declare exchanges
            self.exchange = await self.channel.declare_exchange(
                name=self.EVENTS_EXCHANGE,
                type=ExchangeType.TOPIC,
                durable=True
            )

            # Declare queues
            await self._declare_queue(self.EVENTS_QUEUE)
            await self._declare_queue(self.EMBEDDINGS_QUEUE)
            await self._declare_queue(self.PROCESSING_QUEUE)
            await self._declare_queue(self.DLQ_QUEUE)

            # Bind events queue to exchange
            events_queue = await self.channel.get_queue(self.EVENTS_QUEUE)
            await events_queue.bind(
                exchange=self.exchange,
                routing_key="slack.event.*"
            )

            # Bind processing queue to exchange for workspace tasks
            # Use # wildcard to match workspace.* (one word) and workspace.*.* (multiple words)
            processing_queue = await self.channel.get_queue(self.PROCESSING_QUEUE)
            await processing_queue.bind(
                exchange=self.exchange,
                routing_key="workspace.#"
            )

            self._initialized = True
            logger.info("queue_initialized", rabbitmq_host=settings.rabbitmq_host)

        except Exception as e:
            logger.error("rabbitmq_connection_failed", error=str(e))
            raise

    async def _declare_queue(self, queue_name: str) -> aio_pika.Queue:
        """Declare a durable queue"""
        queue = await self.channel.declare_queue(
            name=queue_name,
            durable=True,
            arguments={
                'x-message-ttl': 86400000,  # 24 hours
                'x-max-length': 100000
            }
        )
        return queue

    async def publish(self, queue: str, message: dict, routing_key: Optional[str] = None) -> bool:
        """Publish message to queue"""
        try:
            if not self._initialized:
                logger.error("queue_not_initialized")
                return False

            body = json.dumps(message).encode()

            # Create message with persistent delivery mode
            aio_message = aio_pika.Message(
                body=body,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type='application/json'
            )

            if routing_key:
                # Publish to exchange with routing key
                await self.exchange.publish(
                    message=aio_message,
                    routing_key=routing_key
                )
                logger.info("message_published_to_exchange", routing_key=routing_key, queue=queue)
            else:
                # Publish directly to queue
                await self.channel.default_exchange.publish(
                    message=aio_message,
                    routing_key=queue
                )
                logger.info("message_published_to_queue", queue=queue)

            return True
        except Exception as e:
            logger.error("queue_publish_error", queue=queue, error=str(e))
            return False

    async def get_queue(self, queue_name: str) -> Optional[aio_pika.Queue]:
        """Get queue object for consumption"""
        try:
            return await self.channel.get_queue(queue_name)
        except Exception as e:
            logger.error("get_queue_error", queue=queue_name, error=str(e))
            return None

    async def close(self):
        """Close RabbitMQ connection"""
        if self.connection and not self.connection.is_closed:
            await self.connection.close()
        self._initialized = False
        logger.info("queue_closed")


# Global queue manager instance
queue_manager = QueueManager()
