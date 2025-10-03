"""Base class for RabbitMQ consumers"""
import json
import pika
from abc import ABC, abstractmethod
from typing import Dict, Any
from app.core.config import settings
from app.core.logging import get_logger
from app.core.queue import QueueManager

logger = get_logger(__name__)


class BaseConsumer(ABC):
    """Base class for RabbitMQ queue consumers"""

    def __init__(self, queue_name: str):
        self.queue_name = queue_name
        self.connection = None
        self.channel = None

    def connect(self):
        """Establish connection to RabbitMQ"""
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

        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

        # Ensure queue exists with the same arguments as declared by QueueManager
        # Mismatched arguments (e.g., x-message-ttl) cause PRECONDITION_FAILED
        self.channel.queue_declare(
            queue=self.queue_name,
            durable=True,
            arguments={
                'x-message-ttl': 86400000,  # 24 hours (must match app.core.queue.QueueManager)
                'x-max-length': 100000
            }
        )

        # Set QoS - prefetch 1 message at a time for fair distribution
        self.channel.basic_qos(prefetch_count=1)

        logger.info("consumer_connected", queue=self.queue_name)

    def start_consuming(self):
        """Start consuming messages from queue"""
        self.channel.basic_consume(
            queue=self.queue_name,
            on_message_callback=self._on_message
        )

        logger.info("consumer_started", queue=self.queue_name)
        try:
            self.channel.start_consuming()
        except KeyboardInterrupt:
            self.channel.stop_consuming()
            logger.info("consumer_stopped", queue=self.queue_name)

    def _on_message(self, ch, method, properties, body):
        """Internal message handler with error handling"""
        try:
            message = json.loads(body)
            logger.info("message_received", queue=self.queue_name)

            # Process message
            success = self.process_message(message)

            if success:
                # Acknowledge message
                ch.basic_ack(delivery_tag=method.delivery_tag)
                logger.info("message_processed", queue=self.queue_name)
            else:
                # Reject and requeue (or send to DLQ after retries)
                retry_count = properties.headers.get('x-retry-count', 0) if properties.headers else 0

                if retry_count < 3:
                    # Requeue with incremented retry count
                    headers = properties.headers or {}
                    headers['x-retry-count'] = retry_count + 1

                    ch.basic_publish(
                        exchange='',
                        routing_key=self.queue_name,
                        body=body,
                        properties=pika.BasicProperties(
                            delivery_mode=2,
                            headers=headers
                        )
                    )
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    logger.warning("message_requeued", queue=self.queue_name, retry=retry_count + 1)
                else:
                    # Send to DLQ
                    ch.basic_publish(
                        exchange='',
                        routing_key=QueueManager.DLQ_QUEUE,
                        body=body,
                        properties=pika.BasicProperties(delivery_mode=2)
                    )
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    logger.error("message_moved_to_dlq", queue=self.queue_name)

        except json.JSONDecodeError as e:
            logger.error("message_decode_error", error=str(e))
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            logger.error("message_processing_error", error=str(e))
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    @abstractmethod
    def process_message(self, message: Dict[str, Any]) -> bool:
        """Process a single message - must be implemented by subclass"""
        pass

    def close(self):
        """Close connection"""
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            logger.info("consumer_closed", queue=self.queue_name)
