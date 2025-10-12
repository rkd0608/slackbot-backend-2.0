"""Initial workspace indexing worker"""
import asyncio
import httpx
from datetime import datetime
from typing import List, Dict, Any
from sqlalchemy import select
from app.models.workspace import Workspace
from app.models.channel import Channel
from app.models.message import Message
from app.core.database import db_manager
from app.core.logging import get_logger
from app.services.message_processor import message_processor
from app.services.embedding_service import embedding_service
from app.core.vector_db import vector_db_manager

logger = get_logger(__name__)


class InitialIndexingWorker:
    """Handles initial indexing of workspace messages"""

    async def index_workspace(self, team_id: str, bot_token: str) -> None:
        """Index all accessible channels and messages for a workspace"""

        try:
            logger.info("initial_indexing_started", team_id=team_id)

            async for db in db_manager.get_session():
                # Get workspace
                stmt = select(Workspace).where(Workspace.team_id == team_id)
                result = await db.execute(stmt)
                workspace = result.scalar_one_or_none()

                if not workspace:
                    logger.error("workspace_not_found", team_id=team_id)
                    return

                # Step 1: Fetch all channels
                channels = await self._fetch_channels(bot_token, team_id)
                logger.info("channels_fetched", team_id=team_id, count=len(channels))

                # Step 2: Store channel info
                await self._store_channels(channels, team_id, db)

                # Step 3: Index messages from each channel
                total_messages = 0
                indexed_channels = 0

                for channel in channels:
                    channel_id = channel["id"]
                    channel_name = channel["name"]
                    is_private = channel.get("is_private", False)
                    is_archived = channel.get("is_archived", False)

                    # Check if should index based on workspace settings
                    from app.services.workspace_service import workspace_service
                    should_index = await workspace_service.should_index_channel(
                        team_id=team_id,
                        channel_id=channel_id,
                        is_private=is_private,
                        is_archived=is_archived,
                        db=db
                    )

                    if not should_index:
                        logger.info(
                            "channel_skipped",
                            team_id=team_id,
                            channel=channel_name,
                            reason="workspace_settings"
                        )
                        continue

                    # Fetch and index messages
                    messages = await self._fetch_channel_history(
                        bot_token=bot_token,
                        channel_id=channel_id,
                        limit=1000  # Limit per channel for initial indexing
                    )

                    if messages:
                        await self._index_messages(
                            messages=messages,
                            channel_id=channel_id,
                            channel_name=channel_name,
                            team_id=team_id,
                            db=db
                        )

                        total_messages += len(messages)
                        indexed_channels += 1

                        logger.info(
                            "channel_indexed",
                            team_id=team_id,
                            channel=channel_name,
                            messages=len(messages)
                        )

                    # Rate limiting - pause between channels
                    await asyncio.sleep(1)

                # Step 4: Build knowledge graph - entity relationships
                logger.info("building_entity_relationships", team_id=team_id)
                relationship_count = await self._build_entity_relationships(team_id, db)

                # Step 5: Calculate user expertise
                logger.info("calculating_user_expertise", team_id=team_id)
                expertise_count = await self._calculate_user_expertise(team_id, db)

                # Step 6: Detect significant events
                logger.info("detecting_significant_events", team_id=team_id)
                event_count = await self._detect_significant_events(team_id, db)

                # Step 7: Update workspace indexing status
                workspace.indexing_status = "complete"
                workspace.indexing_completed_at = datetime.utcnow()
                workspace.total_messages_indexed = total_messages
                workspace.total_channels_indexed = indexed_channels
                await db.commit()

                logger.info(
                    "initial_indexing_completed",
                    team_id=team_id,
                    channels=indexed_channels,
                    messages=total_messages,
                    relationships=relationship_count,
                    expertise_areas=expertise_count,
                    events=event_count
                )

                # Send completion notification to workspace admin
                await self._send_completion_notification(
                    team_id=team_id,
                    bot_token=bot_token,
                    installer_user_id=workspace.installer_user_id,
                    channels_indexed=indexed_channels,
                    messages_indexed=total_messages,
                    knowledge_graph_built=True
                )

        except Exception as e:
            logger.error(
                "initial_indexing_error",
                error=str(e),
                team_id=team_id
            )

            # Update workspace status to failed
            async for db in db_manager.get_session():
                stmt = select(Workspace).where(Workspace.team_id == team_id)
                result = await db.execute(stmt)
                workspace = result.scalar_one_or_none()

                if workspace:
                    workspace.indexing_status = "failed"
                    await db.commit()

            raise

    async def _fetch_channels(
        self,
        bot_token: str,
        team_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch all channels from Slack"""

        channels = []

        async with httpx.AsyncClient() as client:
            cursor = None

            while True:
                params = {
                    "types": "public_channel,private_channel",
                    "exclude_archived": False,
                    "limit": 200
                }

                if cursor:
                    params["cursor"] = cursor

                response = await client.post(
                    "https://slack.com/api/conversations.list",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    data=params
                )

                data = response.json()

                if not data.get("ok"):
                    logger.error(
                        "fetch_channels_error",
                        error=data.get("error"),
                        team_id=team_id
                    )
                    break

                channels.extend(data.get("channels", []))

                # Check for pagination
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        return channels

    async def _store_channels(
        self,
        channels: List[Dict[str, Any]],
        team_id: str,
        db
    ) -> None:
        """Store channel information in database"""

        for channel_data in channels:
            # Check if channel exists
            stmt = select(Channel).where(Channel.channel_id == channel_data["id"])
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                # Update existing channel
                existing.channel_name = channel_data["name"]
                existing.is_private = 1 if channel_data.get("is_private") else 0
                existing.is_archived = 1 if channel_data.get("is_archived") else 0
                existing.member_count = channel_data.get("num_members", 0)
                existing.updated_at = datetime.utcnow()
            else:
                # Create new channel
                channel = Channel(
                    channel_id=channel_data["id"],
                    channel_name=channel_data["name"],
                    team_id=team_id,
                    is_private=1 if channel_data.get("is_private") else 0,
                    is_archived=1 if channel_data.get("is_archived") else 0,
                    member_count=channel_data.get("num_members", 0),
                    topic=channel_data.get("topic", {}).get("value"),
                    purpose=channel_data.get("purpose", {}).get("value"),
                    indexing_status="pending",
                    created_at=datetime.utcnow()
                )
                db.add(channel)

        await db.commit()

    async def _fetch_channel_history(
        self,
        bot_token: str,
        channel_id: str,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Fetch message history from channel"""

        messages = []

        async with httpx.AsyncClient() as client:
            cursor = None
            fetched = 0

            while fetched < limit:
                params = {
                    "channel": channel_id,
                    "limit": min(200, limit - fetched)
                }

                if cursor:
                    params["cursor"] = cursor

                response = await client.post(
                    "https://slack.com/api/conversations.history",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    data=params
                )

                data = response.json()

                if not data.get("ok"):
                    logger.error(
                        "fetch_history_error",
                        error=data.get("error"),
                        channel_id=channel_id
                    )
                    break

                batch = data.get("messages", [])
                messages.extend(batch)
                fetched += len(batch)

                # Check for pagination
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        return messages

    async def _index_messages(
        self,
        messages: List[Dict[str, Any]],
        channel_id: str,
        channel_name: str,
        team_id: str,
        db
    ) -> None:
        """Process and index messages"""

        for msg_data in messages:
            # Skip bot messages and certain subtypes
            if msg_data.get("subtype") in ["bot_message", "channel_join", "channel_leave"]:
                continue

            # Process message using existing pipeline
            await message_processor.process_message(
                message_data={
                    **msg_data,
                    "channel": channel_id,
                    "team": team_id
                },
                channel_id=channel_id,
                channel_name=channel_name,
                team_id=team_id,
                db=db
            )

    async def _build_entity_relationships(self, team_id: str, db) -> int:
        """Build entity relationships from co-occurrence patterns"""

        try:
            from app.models.entity import Entity, EntityRelationship
            from sqlalchemy import and_, func
            from datetime import datetime

            # Get all entities for this workspace's channels
            # First get all channel IDs for this team
            from app.models.channel import Channel
            result = await db.execute(
                select(Channel.channel_id).where(Channel.team_id == team_id)
            )
            team_channels = [row[0] for row in result.all()]

            if not team_channels:
                return 0

            # Get entities that appear in these channels
            result = await db.execute(
                select(Entity).where(
                    func.json_contains(Entity.related_channels, func.json_quote(team_channels[0]))
                )
            )
            entities = result.scalars().all()

            if len(entities) < 2:
                return 0

            relationship_count = 0

            # For each pair of entities, check if they co-occur in messages
            for i, entity1 in enumerate(entities):
                for entity2 in entities[i+1:]:
                    # Check messages containing both entities
                    from app.models.message import Message
                    result = await db.execute(
                        select(func.count(Message.id)).where(
                            and_(
                                Message.team_id == team_id,
                                Message.text_processed.ilike(f"%{entity1.entity_text}%"),
                                Message.text_processed.ilike(f"%{entity2.entity_text}%")
                            )
                        )
                    )
                    co_occurrence_count = result.scalar()

                    if co_occurrence_count > 0:
                        # Calculate confidence score
                        confidence = min(1.0, co_occurrence_count / min(entity1.occurrence_count, entity2.occurrence_count))

                        # Create relationship (ensure entity1.id < entity2.id)
                        if entity1.id > entity2.id:
                            entity1, entity2 = entity2, entity1

                        # Check if relationship exists
                        result = await db.execute(
                            select(EntityRelationship).where(
                                and_(
                                    EntityRelationship.entity_id_1 == entity1.id,
                                    EntityRelationship.entity_id_2 == entity2.id
                                )
                            )
                        )
                        existing = result.scalar_one_or_none()

                        if not existing:
                            relationship = EntityRelationship(
                                entity_id_1=entity1.id,
                                entity_id_2=entity2.id,
                                relationship_type="co_occurs_with",
                                co_occurrence_count=co_occurrence_count,
                                confidence_score=round(confidence, 4),
                                channels=team_channels,
                                first_seen_at=datetime.utcnow(),
                                last_seen_at=datetime.utcnow()
                            )
                            db.add(relationship)
                            relationship_count += 1

            await db.commit()

            logger.info("entity_relationships_built", team_id=team_id, count=relationship_count)
            return relationship_count

        except Exception as e:
            logger.error("build_relationships_error", error=str(e), team_id=team_id)
            return 0

    async def _calculate_user_expertise(self, team_id: str, db) -> int:
        """Calculate user expertise for all users in workspace"""

        try:
            from app.services.expertise_service import expertise_service
            from app.models.user import User

            # Get all users in this workspace
            result = await db.execute(
                select(User.user_id).where(
                    and_(
                        User.team_id == team_id,
                        User.is_bot == False
                    )
                )
            )
            user_ids = [row[0] for row in result.all()]

            total_expertise_areas = 0

            for user_id in user_ids:
                try:
                    expertise_areas = await expertise_service.calculate_user_expertise(
                        user_id=user_id,
                        db=db
                    )
                    total_expertise_areas += len(expertise_areas)
                except Exception as e:
                    logger.error("user_expertise_calc_failed", error=str(e), user_id=user_id)
                    continue

            logger.info("user_expertise_calculated", team_id=team_id, expertise_areas=total_expertise_areas)
            return total_expertise_areas

        except Exception as e:
            logger.error("calculate_expertise_error", error=str(e), team_id=team_id)
            return 0

    async def _detect_significant_events(self, team_id: str, db) -> int:
        """Detect significant events from messages"""

        try:
            from app.services.event_detection_service import event_detection_service
            from app.models.message import Message

            # Get recent messages (last 30 days worth)
            from datetime import timedelta
            cutoff_date = datetime.utcnow() - timedelta(days=30)

            result = await db.execute(
                select(Message).where(
                    and_(
                        Message.team_id == team_id,
                        Message.timestamp >= cutoff_date
                    )
                ).order_by(Message.timestamp.desc()).limit(1000)
            )
            messages = result.scalars().all()

            if not messages:
                return 0

            # Detect events
            events = await event_detection_service.detect_events_from_messages(
                messages=messages,
                db=db
            )

            logger.info("significant_events_detected", team_id=team_id, count=len(events))
            return len(events)

        except Exception as e:
            logger.error("detect_events_error", error=str(e), team_id=team_id)
            return 0

    async def _send_completion_notification(
        self,
        team_id: str,
        bot_token: str,
        installer_user_id: str,
        channels_indexed: int,
        messages_indexed: int,
        knowledge_graph_built: bool = False
    ) -> None:
        """Send DM to installer about indexing completion"""

        try:
            async with httpx.AsyncClient() as client:
                # Open DM with installer
                dm_response = await client.post(
                    "https://slack.com/api/conversations.open",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    json={"users": installer_user_id}
                )

                dm_data = dm_response.json()
                if not dm_data.get("ok"):
                    logger.error("open_dm_error", error=dm_data.get("error"))
                    return

                channel_id = dm_data["channel"]["id"]

                # Build blocks based on what was completed
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "🎉 *Workspace Indexing Complete!*\n\nYour Slack workspace is now fully indexed and ready to use."
                        }
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": f"*Channels Indexed:*\n{channels_indexed}"
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Messages Indexed:*\n{messages_indexed:,}"
                            }
                        ]
                    }
                ]

                if knowledge_graph_built:
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "✨ *Knowledge Graph Built:*\n• Entity relationships mapped\n• User expertise calculated\n• Significant events detected"
                        }
                    })

                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Get Started:*\n• Use `/ask` to ask questions\n• Use `/find` to search messages\n• Mention me for quick queries"
                    }
                })

                # Send notification message
                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    json={
                        "channel": channel_id,
                        "text": f"🎉 Workspace indexing complete!",
                        "blocks": blocks
                    }
                )

                logger.info("completion_notification_sent", team_id=team_id)

        except Exception as e:
            logger.error("send_notification_error", error=str(e), team_id=team_id)


# Global worker instance
initial_indexing_worker = InitialIndexingWorker()
