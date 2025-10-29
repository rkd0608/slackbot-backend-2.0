"""Message processing service"""
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.message import Message
from app.models.thread import Thread
from app.models.user import User
from app.models.channel import Channel
from app.core.logging import get_logger
from app.core.queue import queue_manager
from app.services.code_detector import code_detector

logger = get_logger(__name__)


class MessageProcessor:
    """Processes and enriches Slack messages"""

    # Regex patterns
    URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
    MENTION_PATTERN = re.compile(r'<@([A-Z0-9]+)>')
    CHANNEL_MENTION_PATTERN = re.compile(r'<#([A-Z0-9]+)\|([^>]+)>')

    async def process_message(
        self,
        message_data: Dict[str, Any],
        channel_id: str,
        channel_name: str,
        team_id: str,
        db: AsyncSession
    ) -> Optional[Message]:
        """Process a message (used by initial indexing worker)"""
        # Enrich message_data with channel info if not present
        if "channel" not in message_data:
            message_data["channel"] = channel_id
        if "channel_name" not in message_data:
            message_data["channel_name"] = channel_name

        # Call the main event processor
        return await self.process_message_event(
            event=message_data,
            team_id=team_id,
            db=db
        )

    async def process_message_event(
        self,
        event: Dict[str, Any],
        team_id: str,
        db: AsyncSession
    ) -> Optional[Message]:
        """Process a message event and store in database"""

        # Extract message data
        message_data = self._extract_message_data(event, team_id)

        # Enrich with user-name from database
        if message_data.get("user_id") and not message_data.get("user_name"):
            user_name = await self._get_user_name(message_data["user_id"], db)
            message_data["user_name"] = user_name

        # Enrich with channel_name from database if not provided
        if message_data.get("channel_id") and not message_data.get("channel_name"):
            channel_name = await self._get_channel_name(message_data["channel_id"], team_id, db)
            message_data["channel_name"] = channel_name

        # If we cannot derive a valid user identifier, skip persisting
        if not message_data.get("user_id"):
            logger.warning("message_skipped_missing_user", message_id=message_data.get("message_id"))
            return None

        # Check if message already exists (idempotency)
        existing = await db.execute(
            select(Message).where(Message.message_id == message_data["message_id"])
        )
        existing_message = existing.scalar_one_or_none()

        if existing_message:
            # Update existing message (e.g., edited)
            if event.get("subtype") == "message_changed":
                await self._update_message(existing_message, message_data, db)
                return existing_message
            else:
                logger.info("message_already_exists", message_id=message_data["message_id"])
                return existing_message

        # Create new message
        message = Message(**message_data)
        db.add(message)
        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error("message_insert_failed", message_id=message_data.get("message_id"), error=str(e))
            return None
        await db.refresh(message)

        logger.info(
            "message_created",
            message_id=message.message_id,
            channel_id=message.channel_id,
            has_thread=bool(message.thread_ts)
        )

        # Update thread if this is a reply
        if message.thread_ts and message.thread_ts != message.message_id:
            await self._update_thread(message, db)

        # Extract and store entities (company-specific intelligence)
        if message.text_processed and len(message.text_processed) > 10:
            try:
                from app.services.entity_service import entity_service
                await entity_service.extract_and_store_entities(
                    message_text=message.text_processed,
                    message_id=message.message_id,
                    channel_id=message.channel_id,
                    user_id=message.user_id,
                    team_id=message.team_id,
                    db=db
                )
            except Exception as e:
                logger.error("entity_extraction_failed", error=str(e), message_id=message.message_id)

        # Extract and embed code snippets (code intelligence)
        if message.has_code:
            try:
                from app.core.config import settings
                if settings.enable_code_intelligence:
                    from app.services.code_extraction_service import code_extraction_service
                    from app.services.code_embedding_service import code_embedding_service

                    logger.info("code_extraction_started", message_id=message.message_id)

                    # Extract and store code snippets from message
                    snippet_ids = await code_extraction_service.extract_and_store_code(
                        message_id=message.message_id,
                        text=message.text,
                        db=db
                    )

                    logger.info(
                        "code_extraction_completed",
                        message_id=message.message_id,
                        snippet_count=len(snippet_ids)
                    )

                    # Generate embeddings for each code snippet
                    for snippet_id in snippet_ids:
                        try:
                            success = await code_embedding_service.embed_code_snippet(
                                snippet_id=snippet_id,
                                db=db
                            )
                            if success:
                                logger.info("code_embedded", snippet_id=snippet_id)
                            else:
                                logger.warning("code_embedding_failed", snippet_id=snippet_id)
                        except Exception as embed_error:
                            logger.error(
                                "code_embedding_error",
                                snippet_id=snippet_id,
                                error=str(embed_error)
                            )
            except Exception as e:
                logger.error("code_intelligence_failed", error=str(e), message_id=message.message_id)

        # Queue for embedding generation
        await queue_manager.publish(
            queue=queue_manager.EMBEDDINGS_QUEUE,
            message={
                "message_id": message.message_id,
                "text": message.text_processed,
                "channel_id": message.channel_id,
                "timestamp": message.timestamp.isoformat()
            }
        )

        return message

    def _extract_message_data(self, event: Dict[str, Any], team_id: str) -> Dict[str, Any]:
        """Extract and enrich message data from Slack event"""

        text = event.get("text", "")
        ts = event.get("ts") or event.get("event_ts")

        # Parse message content - use CodeDetector for robust code detection
        code_blocks = code_detector.detect_code_blocks(text)
        code_languages = list(set([cb.get("language") for cb in code_blocks if cb.get("language") and cb.get("language") != "unknown"]))
        has_code = len(code_languages) > 0

        links = self._extract_links(text)
        mentioned_users = self._extract_mentions(text)

        # Process text (expand mentions, clean formatting)
        text_processed = self._process_text(text)

        # Calculate importance score
        importance_score = self._calculate_importance(event)

        # Extract file information
        files = event.get("files", [])
        file_ids = [f.get("id") for f in files if f.get("id")]

        # Thread information
        thread_ts = event.get("thread_ts")
        is_thread_parent = (thread_ts == ts) if thread_ts else False

        # Resolve user identity: prefer real user, otherwise bot, otherwise system
        user_id = event.get("user")
        user_name = event.get("username")
        if not user_id:
            bot_id = event.get("bot_id")
            if bot_id:
                user_id = f"BOT:{bot_id}"
                user_name = user_name or "bot"
            else:
                # Leave as None; caller will decide to skip
                user_id = None

        return {
            "message_id": ts,
            "channel_id": event.get("channel"),
            "channel_name": event.get("channel_name"),
            "user_id": user_id,
            "user_name": user_name,
            "team_id": team_id,
            "thread_ts": thread_ts,
            "is_thread_parent": is_thread_parent,
            "text": text,
            "text_processed": text_processed,
            "message_type": event.get("type", "message"),
            "subtype": event.get("subtype"),
            "timestamp": datetime.fromtimestamp(float(ts)),
            "has_code": has_code,
            "code_languages": code_languages if code_languages else None,
            "has_files": len(file_ids) > 0,
            "file_ids": file_ids if file_ids else None,
            "has_links": len(links) > 0,
            "links": links if links else None,
            "reaction_count": 0,  # Will be updated by reaction events
            "reactions": None,
            "mentioned_users": mentioned_users if mentioned_users else None,
            "importance_score": importance_score,
            "raw_json": event
        }

    def _extract_links(self, text: str) -> List[str]:
        """Extract URLs from message"""
        # Slack format: <https://example.com|label> or <https://example.com>
        slack_links = re.findall(r'<(https?://[^|>]+)', text)
        regular_links = self.URL_PATTERN.findall(text)
        return list(set(slack_links + regular_links))

    def _extract_mentions(self, text: str) -> List[str]:
        """Extract user mentions from message"""
        return self.MENTION_PATTERN.findall(text)

    def _process_text(self, text: str) -> str:
        """Clean and process message text"""
        # Remove Slack formatting
        processed = text

        # Expand channel mentions
        processed = self.CHANNEL_MENTION_PATTERN.sub(r'#\2', processed)

        # Keep user mentions as-is for now (could expand with usernames)

        # Clean up extra whitespace
        processed = re.sub(r'\s+', ' ', processed).strip()

        return processed

    def _calculate_importance(self, event: Dict[str, Any]) -> float:
        """Calculate message importance score"""
        score = 0.0

        # Base score
        score += 1.0

        # Has reactions
        reactions = event.get("reactions", [])
        if reactions:
            total_reactions = sum(r.get("count", 0) for r in reactions)
            score += min(total_reactions * 0.5, 5.0)

        # Has thread replies
        reply_count = event.get("reply_count", 0)
        if reply_count:
            score += min(reply_count * 0.3, 3.0)

        # Has files
        if event.get("files"):
            score += 2.0

        # Has links
        text = event.get("text", "")
        if self.URL_PATTERN.search(text):
            score += 1.0

        # Has code - use CodeDetector for accurate detection
        code_blocks = code_detector.detect_code_blocks(text)
        if len(code_blocks) > 0:
            score += 1.5

        # Message length (longer messages often more informative)
        if len(text) > 500:
            score += 1.0

        return round(score, 2)

    async def _update_message(
        self,
        message: Message,
        new_data: Dict[str, Any],
        db: AsyncSession
    ):
        """Update existing message (e.g., after edit)"""
        # Store edit history
        edit_history = message.edit_history or []
        edit_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "old_text": message.text
        })

        # Update fields
        message.text = new_data["text"]
        message.text_processed = new_data["text_processed"]
        message.edited_timestamp = datetime.utcnow()
        message.edit_history = edit_history
        message.raw_json = new_data["raw_json"]

        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error("message_update_failed", message_id=message.message_id, error=str(e))
            return
        logger.info("message_updated", message_id=message.message_id)

    async def _update_thread(self, message: Message, db: AsyncSession):
        """Update thread statistics when new reply added"""
        result = await db.execute(
            select(Thread).where(Thread.thread_ts == message.thread_ts)
        )
        thread = result.scalar_one_or_none()

        if thread:
            # Update existing thread
            thread.reply_count += 1
            thread.last_reply_at = message.timestamp

            # Add participant if new
            participants = thread.participant_ids or []
            if message.user_id not in participants:
                participants.append(message.user_id)
                thread.participant_ids = participants
                thread.participant_count = len(participants)

            await db.commit()
            logger.info("thread_updated", thread_ts=message.thread_ts)

    async def _get_user_name(self, user_id: str, db: AsyncSession) -> Optional[str]:
        """Get user name from Users table, sync if not found"""
        try:
            # Check if user exists in database
            result = await db.execute(
                select(User).where(User.user_id == user_id)
            )
            user = result.scalar_one_or_none()

            if user and user.display_name:
                return user.display_name
            elif user and user.real_name:
                return user.real_name
            elif user and user.username:
                return user.username

            # User not in DB or no name - sync from Slack
            from app.services.sync_service import sync_service
            synced_user = await sync_service.sync_user(user_id, db)

            if synced_user:
                return synced_user.display_name or synced_user.real_name or synced_user.username

            logger.warning("user_name_not_found", user_id=user_id)
            return None

        except Exception as e:
            logger.error("get_user_name_error", user_id=user_id, error=str(e))
            return None

    async def _get_channel_name(self, channel_id: str, team_id: str, db: AsyncSession) -> Optional[str]:
        """Get channel name from Channels table, auto-sync if missing"""
        try:
            # Check if channel exists in database
            result = await db.execute(
                select(Channel).where(
                    Channel.channel_id == channel_id,
                    Channel.team_id == team_id
                )
            )
            channel = result.scalar_one_or_none()

            if channel and channel.channel_name:
                return channel.channel_name

            # Channel not in DB - try to sync from Slack API
            logger.info(
                "channel_not_in_db_syncing",
                channel_id=channel_id,
                team_id=team_id
            )

            from app.services.sync_service import sync_service
            synced_channel = await sync_service.sync_channel(channel_id, db)

            if synced_channel and synced_channel.channel_name:
                logger.info(
                    "channel_auto_synced",
                    channel_id=channel_id,
                    channel_name=synced_channel.channel_name,
                    is_private=bool(synced_channel.is_private)
                )
                return synced_channel.channel_name

            logger.warning("channel_sync_failed", channel_id=channel_id, team_id=team_id)
            return None

        except Exception as e:
            logger.error("get_channel_name_error", channel_id=channel_id, error=str(e))
            return None


# Global message processor instance
message_processor = MessageProcessor()
