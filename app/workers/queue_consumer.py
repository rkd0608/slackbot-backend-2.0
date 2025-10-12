"""RabbitMQ queue consumer for background tasks"""
import asyncio
import json
from typing import Callable, Dict, Any
from app.core.queue import queue_manager
from app.core.database import db_manager
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueueConsumer:
    """Consumes messages from RabbitMQ queues and processes them"""

    def __init__(self):
        self.running = False
        self.handlers: Dict[str, Callable] = {}
        self._consumer_tasks = []

    def register_handler(self, routing_key: str, handler: Callable):
        """Register a handler function for a specific routing key"""
        self.handlers[routing_key] = handler
        logger.info("handler_registered", routing_key=routing_key)

    async def start(self):
        """Start consuming messages from all queues"""

        if self.running:
            logger.warning("queue_consumer_already_running")
            return

        self.running = True

        # Register all handlers
        self._register_all_handlers()

        # Start consuming from processing queue
        processing_task = asyncio.create_task(
            self._consume_queue(
                queue=queue_manager.PROCESSING_QUEUE,
                queue_name="processing"
            )
        )
        self._consumer_tasks.append(processing_task)

        # Start consuming from events queue
        events_task = asyncio.create_task(
            self._consume_queue(
                queue=queue_manager.EVENTS_QUEUE,
                queue_name="events"
            )
        )
        self._consumer_tasks.append(events_task)

        # Start consuming from embeddings queue
        embeddings_task = asyncio.create_task(
            self._consume_queue(
                queue=queue_manager.EMBEDDINGS_QUEUE,
                queue_name="embeddings"
            )
        )
        self._consumer_tasks.append(embeddings_task)

        logger.info("queue_consumer_started", queues=["processing", "events", "embeddings"])

    async def stop(self):
        """Stop consuming messages"""
        self.running = False

        # Cancel all consumer tasks
        for task in self._consumer_tasks:
            task.cancel()

        logger.info("queue_consumer_stopped")

    def _register_all_handlers(self):
        """Register all task handlers"""

        # Import handlers to avoid circular imports
        from app.workers.initial_indexing import initial_indexing_worker
        from app.services.installation_handler import installation_handler

        # Workspace indexing
        self.register_handler(
            "workspace.indexing.initial",
            self._handle_initial_indexing
        )

        # Installation events
        self.register_handler(
            "slack.event.app_uninstalled",
            self._handle_app_uninstalled
        )

        self.register_handler(
            "slack.event.tokens_revoked",
            self._handle_tokens_revoked
        )

        # Workspace deletion
        self.register_handler(
            "workspace.deletion.schedule",
            self._handle_workspace_deletion_schedule
        )

        # Message embedding
        self.register_handler(
            queue_manager.EMBEDDINGS_QUEUE,  # "slack.embeddings"
            self._handle_message_embedding
        )

    async def _consume_queue(self, queue: str, queue_name: str):
        """Consume messages from a specific queue"""

        try:
            while self.running:
                try:
                    # Get message from queue (non-blocking with timeout)
                    method_frame, properties, body = queue_manager.channel.basic_get(
                        queue=queue,
                        auto_ack=False
                    )

                    if method_frame:
                        # Process message
                        await self._process_message(
                            method_frame=method_frame,
                            properties=properties,
                            body=body,
                            queue_name=queue_name
                        )
                    else:
                        # No message available, wait a bit
                        await asyncio.sleep(1)

                except Exception as e:
                    logger.error(
                        "queue_consume_error",
                        queue=queue_name,
                        error=str(e)
                    )
                    await asyncio.sleep(5)  # Wait before retrying

        except asyncio.CancelledError:
            logger.info("queue_consumer_cancelled", queue=queue_name)

    async def _process_message(
        self,
        method_frame,
        properties,
        body: bytes,
        queue_name: str
    ):
        """Process a single message from the queue"""

        try:
            # Parse message
            message = json.loads(body.decode())
            routing_key = method_frame.routing_key

            logger.info(
                "message_received",
                queue=queue_name,
                routing_key=routing_key,
                message=message
            )

            # Find handler - try routing key first, then queue name
            handler = self.handlers.get(routing_key)

            # For direct queue publishing (embeddings), use queue name as key
            if not handler and routing_key == queue_name:
                handler = self.handlers.get(queue_name)

            if handler:
                # Execute handler
                await handler(message)

                # Acknowledge message
                queue_manager.channel.basic_ack(method_frame.delivery_tag)

                logger.info(
                    "message_processed",
                    queue=queue_name,
                    routing_key=routing_key
                )
            else:
                logger.warning(
                    "no_handler_found",
                    routing_key=routing_key,
                    queue_name=queue_name,
                    available_handlers=list(self.handlers.keys())
                )
                # Acknowledge anyway to remove from queue
                queue_manager.channel.basic_ack(method_frame.delivery_tag)

        except json.JSONDecodeError as e:
            logger.error("message_parse_error", error=str(e), body=body)
            # Reject and don't requeue malformed messages
            queue_manager.channel.basic_nack(
                method_frame.delivery_tag,
                requeue=False
            )

        except Exception as e:
            logger.error(
                "message_processing_error",
                error=str(e),
                queue=queue_name,
                routing_key=method_frame.routing_key if method_frame else None
            )
            # Requeue for retry
            queue_manager.channel.basic_nack(
                method_frame.delivery_tag,
                requeue=True
            )

    # ====================
    # MESSAGE HANDLERS
    # ====================

    async def _handle_initial_indexing(self, message: Dict[str, Any]):
        """Handle initial workspace indexing task"""

        team_id = message.get("team_id")
        bot_token = message.get("bot_token")

        if not team_id or not bot_token:
            logger.error("invalid_indexing_message", message=message)
            return

        logger.info("starting_initial_indexing", team_id=team_id)

        from app.workers.initial_indexing import initial_indexing_worker

        try:
            await initial_indexing_worker.index_workspace(team_id, bot_token)
            logger.info("initial_indexing_completed", team_id=team_id)

        except Exception as e:
            logger.error(
                "initial_indexing_failed",
                error=str(e),
                team_id=team_id
            )
            raise

    async def _handle_app_uninstalled(self, message: Dict[str, Any]):
        """Handle app uninstallation event"""

        team_id = message.get("team_id")

        if not team_id:
            logger.error("invalid_uninstall_message", message=message)
            return

        logger.info("processing_app_uninstall", team_id=team_id)

        from app.services.installation_handler import installation_handler

        try:
            async for db in db_manager.get_session():
                await installation_handler.handle_app_uninstalled(team_id, db)

            logger.info("app_uninstall_processed", team_id=team_id)

        except Exception as e:
            logger.error(
                "app_uninstall_processing_failed",
                error=str(e),
                team_id=team_id
            )
            raise

    async def _handle_tokens_revoked(self, message: Dict[str, Any]):
        """Handle token revocation event"""

        team_id = message.get("team_id")
        tokens = message.get("tokens", {})

        if not team_id:
            logger.error("invalid_token_revoke_message", message=message)
            return

        logger.info("processing_token_revocation", team_id=team_id)

        from app.services.installation_handler import installation_handler

        try:
            async for db in db_manager.get_session():
                await installation_handler.handle_tokens_revoked(team_id, tokens, db)

            logger.info("token_revocation_processed", team_id=team_id)

        except Exception as e:
            logger.error(
                "token_revocation_processing_failed",
                error=str(e),
                team_id=team_id
            )
            raise

    async def _handle_workspace_deletion_schedule(self, message: Dict[str, Any]):
        """Handle scheduled workspace deletion"""

        team_id = message.get("team_id")
        deletion_date = message.get("deletion_date")

        if not team_id:
            logger.error("invalid_deletion_message", message=message)
            return

        logger.info(
            "workspace_deletion_scheduled",
            team_id=team_id,
            deletion_date=deletion_date
        )

        # In production, this would schedule actual deletion after retention period
        # For now, just log
        # TODO: Implement actual deletion logic with 7-day grace period

        try:
            async for db in db_manager.get_session():
                from app.models.workspace import Workspace, InstallationLog
                from sqlalchemy import select
                from datetime import datetime

                # Log the deletion schedule
                log = InstallationLog(
                    team_id=team_id,
                    event_type="deletion_scheduled",
                    event_data={
                        "scheduled_at": datetime.utcnow().isoformat(),
                        "deletion_date": deletion_date
                    },
                    created_at=datetime.utcnow()
                )
                db.add(log)
                await db.commit()

                logger.info("workspace_deletion_logged", team_id=team_id)

        except Exception as e:
            logger.error(
                "workspace_deletion_schedule_failed",
                error=str(e),
                team_id=team_id
            )

    async def _handle_message_embedding(self, message: Dict[str, Any]):
        """Handle message embedding generation"""

        message_id = message.get("message_id")
        text = message.get("text")

        if not message_id or not text:
            logger.error("invalid_embedding_message", message=message)
            return

        logger.info("generating_message_embedding", message_id=message_id)

        try:
            from app.services.embedding_service import embedding_service

            async for db in db_manager.get_session():
                # Generate and store embedding
                await embedding_service.embed_message(
                    message_id=message_id,
                    db=db
                )

                logger.info("message_embedding_generated", message_id=message_id)
                break  # Only use first session

        except Exception as e:
            logger.error(
                "message_embedding_failed",
                error=str(e),
                message_id=message_id
            )
            raise


# Global queue consumer instance
queue_consumer = QueueConsumer()
