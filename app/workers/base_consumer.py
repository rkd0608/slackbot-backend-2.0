"""Base class for async RabbitMQ consumers using aio_pika"""
import json
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import aio_pika
from aio_pika import ExchangeType
from app.core.config import settings
from app.core.logging import get_logger
from app.core.queue import QueueManager

logger = get_logger(__name__)


class BaseConsumer(ABC):
    """Base class for async RabbitMQ queue consumers"""

    def __init__(self, queue_name: str):
        self.queue_name = queue_name
        self.connection: Optional[aio_pika.RobustConnection] = None
        self.channel: Optional[aio_pika.Channel] = None
        self.queue: Optional[aio_pika.Queue] = None

    async def connect(self):
        """Establish async connection to RabbitMQ"""
        rabbitmq_url = (
            f"amqp://{settings.rabbitmq_user}:{settings.rabbitmq_password}"
            f"@{settings.rabbitmq_host}:{settings.rabbitmq_port}/{settings.rabbitmq_vhost}"
        )

        self.connection = await aio_pika.connect_robust(
            rabbitmq_url,
            heartbeat=600,
            timeout=30
        )

        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=1)

        # Declare queue with same arguments as QueueManager
        self.queue = await self.channel.declare_queue(
            name=self.queue_name,
            durable=True,
            arguments={
                'x-message-ttl': 86400000,  # 24 hours
                'x-max-length': 100000
            }
        )

        logger.info("consumer_connected", queue=self.queue_name)

    async def start_consuming(self):
        """Start consuming messages from queue"""
        if not self.queue:
            raise RuntimeError("Consumer not connected. Call connect() first.")

        logger.info("consumer_started", queue=self.queue_name)

        async with self.queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    await self._on_message(message)

    async def _on_message(self, message: aio_pika.IncomingMessage):
        """Internal message handler with error handling"""
        try:
            logger.info("raw_message_received", queue=self.queue_name, body_length=len(message.body))
            body = json.loads(message.body.decode())
            logger.info("message_received", queue=self.queue_name, message_preview=str(body)[:200], message_type=body.get("type", "unknown"))

            # Process message
            try:
                success = await self.process_message(body)
            except Exception as proc_error:
                logger.error(
                    "process_message_exception",
                    queue=self.queue_name,
                    error=str(proc_error),
                    error_type=type(proc_error).__name__
                )
                success = False

            if success:
                # Message auto-ACKed by async with message.process()
                logger.info("message_processed", queue=self.queue_name)
            else:
                # Get retry count from headers
                retry_count = 0
                if message.headers and 'x-retry-count' in message.headers:
                    retry_count = message.headers['x-retry-count']

                if retry_count < 3:
                    # Requeue with incremented retry count
                    headers = message.headers or {}
                    headers['x-retry-count'] = retry_count + 1

                    await self.channel.default_exchange.publish(
                        aio_pika.Message(
                            body=message.body,
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                            headers=headers
                        ),
                        routing_key=self.queue_name
                    )
                    logger.warning("message_requeued", queue=self.queue_name, retry=retry_count + 1)
                else:
                    # Send to DLQ
                    await self.channel.default_exchange.publish(
                        aio_pika.Message(
                            body=message.body,
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                        ),
                        routing_key=QueueManager.DLQ_QUEUE
                    )
                    logger.error("message_moved_to_dlq", queue=self.queue_name)

        except json.JSONDecodeError as e:
            logger.error("message_decode_error", error=str(e))
            # Auto-ACKed, message discarded
        except Exception as e:
            logger.error("message_processing_error", error=str(e))
            # Auto-NACKed by context manager on exception

    @abstractmethod
    async def process_message(self, message: Dict[str, Any]) -> bool:
        """Process a single message - must be implemented by subclass"""
        pass

    async def close(self):
        """Close connection"""
        if self.connection and not self.connection.is_closed:
            await self.connection.close()
            logger.info("consumer_closed", queue=self.queue_name)
