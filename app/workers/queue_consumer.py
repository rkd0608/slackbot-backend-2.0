"""RabbitMQ queue consumer for background tasks with aio-pika"""
import asyncio
import json
from typing import Callable, Dict, Any
import aio_pika
from app.core.queue import queue_manager
from app.core.database import db_manager
from app.core.logging import get_logger

logger = get_logger(__name__)


class QueueConsumer:
    """Consumes messages from RabbitMQ queues and processes them"""

    def __init__(self):
        self.running = False
        self.handlers: Dict[str, Callable] = {}
        self._consumer_tags = []

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

        # Start consuming from each queue
        await self._start_consuming(queue_manager.PROCESSING_QUEUE, "processing")
        await self._start_consuming(queue_manager.EVENTS_QUEUE, "events")
        await self._start_consuming(queue_manager.EMBEDDINGS_QUEUE, "embeddings")

        logger.info("queue_consumer_started", queues=["processing", "events", "embeddings"])

    async def _start_consuming(self, queue_name: str, consumer_name: str):
        """Start consuming from a specific queue"""
        try:
            queue = await queue_manager.get_queue(queue_name)
            if not queue:
                logger.error("queue_not_found", queue=queue_name)
                return

            # Start a background task to consume messages using async iterator
            asyncio.create_task(self._consume_loop(queue, consumer_name, queue_name))

            logger.info("queue_consumer_registered", queue=queue_name, consumer=consumer_name)

        except Exception as e:
            logger.error("queue_consume_start_error", queue=queue_name, error=str(e))

    async def _consume_loop(self, queue: aio_pika.Queue, consumer_name: str, queue_name: str):
        """Consume messages from queue in a loop"""
        logger.info("consume_loop_started", consumer=consumer_name, queue=queue_name)

        try:
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    logger.info("message_received_in_loop", consumer=consumer_name, queue=queue_name)
                    try:
                        async with message.process():
                            await self._process_message(message, consumer_name)
                    except Exception as e:
                        logger.error("message_processing_error_in_loop", error=str(e), consumer=consumer_name)
                        # Message will be requeued on exception
        except Exception as e:
            logger.error("consume_loop_error", error=str(e), consumer=consumer_name, queue=queue_name)
            # If loop crashes, try to restart after a delay
            if self.running:
                logger.info("restarting_consume_loop", consumer=consumer_name, queue=queue_name)
                await asyncio.sleep(5)
                await self._start_consuming(queue_name, consumer_name)

    def _make_callback(self, consumer_name: str):
        """Create a callback function for message processing"""
        async def callback(message: aio_pika.IncomingMessage):
            logger.info("callback_invoked", consumer=consumer_name)
            try:
                await self._process_message(message, consumer_name)
            except Exception as e:
                logger.error("callback_error", consumer=consumer_name, error=str(e), exc_info=True)
                await message.reject(requeue=True)
        return callback

    async def stop(self):
        """Stop consuming messages"""
        self.running = False
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

        # Message events (real-time messages from Slack)
        self.register_handler(
            "slack.event.message",
            self._handle_slack_message_event
        )

        # App mention events (bot is mentioned in a channel)
        self.register_handler(
            "slack.event.app_mention",
            self._handle_app_mention_event
        )

        # Message embedding
        self.register_handler(
            queue_manager.EMBEDDINGS_QUEUE,  # "slack.embeddings"
            self._handle_message_embedding
        )

    async def _process_message(
        self,
        message: aio_pika.IncomingMessage,
        queue_name: str
    ):
        """Process a single message from the queue"""

        async with message.process():
            try:
                # Parse message
                body = json.loads(message.body.decode())
                routing_key = message.routing_key or queue_name

                logger.info(
                    "message_received",
                    queue=queue_name,
                    routing_key=routing_key,
                    message_body=body,
                    has_routing_key=bool(message.routing_key)
                )

                # Find handler - try routing key first, then queue name
                handler = self.handlers.get(routing_key)

                # For direct queue publishing (embeddings), use queue name as key
                if not handler and routing_key == queue_name:
                    handler = self.handlers.get(queue_name)

                if handler:
                    # Execute handler
                    await handler(body)

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

            except json.JSONDecodeError as e:
                logger.error("message_parse_error", error=str(e))
                # Message will be rejected (not requeued) on context exit

            except Exception as e:
                logger.error(
                    "message_processing_error",
                    error=str(e),
                    queue=queue_name
                )
                # Re-raise to trigger message requeue
                raise

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

    async def _handle_slack_message_event(self, message: Dict[str, Any]):
        """Handle real-time Slack message events"""

        event = message.get("event")
        team_id = message.get("team_id")

        if not event or not team_id:
            logger.error("invalid_message_event", message=message)
            return

        # Skip bot messages and message subtypes we don't want to index
        if event.get("subtype") in ["bot_message", "channel_join", "channel_leave"]:
            logger.info("skipping_message_subtype", subtype=event.get("subtype"))
            return

        # Skip messages from the bot itself to avoid infinite loops
        if event.get("bot_id"):
            logger.info("skipping_bot_message", bot_id=event.get("bot_id"))
            return

        logger.info(
            "processing_slack_message",
            team_id=team_id,
            channel=event.get("channel"),
            user=event.get("user"),
            channel_type=event.get("channel_type")
        )

        try:
            from app.services.message_processor import message_processor
            from app.services.bot_interaction import bot_interaction_service

            async for db in db_manager.get_session():
                # Check if this is a DM (Direct Message)
                channel_type = event.get("channel_type")
                is_dm = channel_type == "im"

                # Process and store the message
                stored_message = await message_processor.process_message_event(
                    event=event,
                    team_id=team_id,
                    db=db
                )

                if stored_message:
                    logger.info(
                        "slack_message_processed",
                        message_id=stored_message.message_id,
                        channel_id=stored_message.channel_id,
                        channel_name=stored_message.channel_name,
                        is_dm=is_dm
                    )
                else:
                    logger.warning("slack_message_not_stored", event_data=event)

                # If this is a DM, handle as bot interaction (auto-respond)
                if is_dm and stored_message:
                    logger.info(
                        "handling_dm_interaction",
                        message_id=stored_message.message_id,
                        user_id=event.get("user")
                    )
                    await bot_interaction_service.handle_message_event(event, db)

                break  # Only use first session

        except Exception as e:
            logger.error(
                "slack_message_processing_failed",
                error=str(e),
                team_id=team_id,
                channel=event.get("channel")
            )
            raise

    async def _handle_app_mention_event(self, message: Dict[str, Any]):
        """Handle app mention event - bot is mentioned in a channel or thread"""

        event = message.get("event")
        team_id = message.get("team_id")

        if not event or not team_id:
            logger.error("invalid_app_mention_event", message=message)
            return

        logger.info(
            "processing_app_mention",
            team_id=team_id,
            channel=event.get("channel"),
            user=event.get("user"),
            thread_ts=event.get("thread_ts")
        )

        try:
            from app.services.message_processor import message_processor
            from app.services.bot_interaction import bot_interaction_service

            async for db in db_manager.get_session():
                # First, process and store the mention message
                stored_message = await message_processor.process_message_event(
                    event=event,
                    team_id=team_id,
                    db=db
                )

                if stored_message:
                    logger.info(
                        "app_mention_message_stored",
                        message_id=stored_message.message_id,
                        channel_id=stored_message.channel_id
                    )

                # Then handle bot interaction (generate response)
                await bot_interaction_service.handle_message_event(event, db)

                logger.info("app_mention_handled", user=event.get("user"), channel=event.get("channel"))
                break  # Only use first session

        except Exception as e:
            logger.error(
                "app_mention_processing_failed",
                error=str(e),
                team_id=team_id,
                channel=event.get("channel")
            )
            raise

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
