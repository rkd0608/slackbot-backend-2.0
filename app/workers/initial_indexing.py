"""Initial workspace indexing worker"""
import asyncio
import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy import select, or_, and_
from app.models.workspace import Workspace
from app.models.channel import Channel
from app.models.message import Message
from app.models.integration_status import IntegrationStatus
from app.core.database import db_manager
from app.core.logging import get_logger
from app.services.message_processor import message_processor
from app.services.embedding_service import embedding_service
from app.core.vector_db import vector_db_manager

logger = get_logger(__name__)

# Checkpoint configuration
CHECKPOINT_INTERVAL = 100  # Save checkpoint every 100 messages


class InitialIndexingWorker:
    """Handles initial indexing of workspace messages"""

    async def index_workspace(self, team_id: str, user_token: str, bot_token: Optional[str] = None) -> None:
        """
        Index all accessible channels and messages for a workspace using Glean-style user token

        Args:
            team_id: Slack team/workspace ID
            user_token: Installer's user access token (provides broad access for indexing)
            bot_token: Bot token (optional, kept for backward compatibility)

        Note: Uses user_token for all data fetching to maximize data access.
              Installer (typically admin/owner) has broadest channel access.
        """

        logger.info("initial_indexing_started", team_id=team_id, using_user_token=True)

        async for db in db_manager.get_session():
            try:
                # Get workspace
                stmt = select(Workspace).where(Workspace.team_id == team_id)
                result = await db.execute(stmt)
                workspace = result.scalar_one_or_none()

                if not workspace:
                    logger.error("workspace_not_found", team_id=team_id)
                    return

                # Get or create IntegrationStatus for Slack
                stmt = select(IntegrationStatus).where(
                    and_(
                        IntegrationStatus.team_id == team_id,
                        IntegrationStatus.source_type == "slack"
                    )
                )
                result = await db.execute(stmt)
                integration_status = result.scalar_one_or_none()

                if not integration_status:
                    logger.error("integration_status_not_found", team_id=team_id)
                    return

                # Update status to in_progress
                integration_status.indexing_status = "in_progress"
                integration_status.indexing_started_at = datetime.utcnow()

                # Reset counters when starting fresh indexing (no resume checkpoint)
                checkpoint = integration_status.last_checkpoint or {}
                resume_from_channel = checkpoint.get("last_channel_id")

                if not resume_from_channel:
                    # Fresh start - reset all counters
                    integration_status.messages_indexed = 0
                    integration_status.items_indexed = 0
                    logger.info(
                        "indexing_counters_reset",
                        team_id=team_id,
                        reason="fresh_start"
                    )

                messages_already_indexed = integration_status.messages_indexed
                await db.commit()

                logger.info(
                    "indexing_checkpoint_loaded",
                    team_id=team_id,
                    resume_from_channel=resume_from_channel,
                    messages_indexed=messages_already_indexed
                )

                # Check if specific channels were selected during onboarding
                selected_channel_ids = None
                if integration_status.config:
                    selected_channel_ids = integration_status.config.get("selected_channels")

                # Step 1: Fetch channels
                if selected_channel_ids:
                    # Only fetch selected channels
                    logger.info(
                        "fetching_selected_channels",
                        team_id=team_id,
                        selected_count=len(selected_channel_ids)
                    )
                    channels = await self._fetch_selected_channels(user_token, team_id, selected_channel_ids)
                else:
                    # Fetch all channels (backward compatibility)
                    logger.info(
                        "fetching_all_channels",
                        team_id=team_id
                    )
                    channels = await self._fetch_channels(user_token, team_id)

                logger.info("channels_fetched", team_id=team_id, count=len(channels))

                # Step 2: Store channel info
                await self._store_channels(channels, team_id, db)

                # Step 2.5: Collect all user IDs from messages and sync users proactively
                logger.info("collecting_user_ids_for_sync", team_id=team_id)
                all_user_ids = set()

                # First pass: collect all user IDs from messages without processing
                for channel in channels:
                    channel_id = channel["id"]
                    is_private = channel.get("is_private", False)
                    is_archived = channel.get("is_archived", False)
                    is_dm = channel.get("is_im", False) or channel_id.startswith("D")
                    is_mpim = channel.get("is_mpim", False)

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
                        continue

                    # Join channel if needed (skip DMs and MPIMs - already have access)
                    if not is_private and not is_dm and not is_mpim and not channel.get("is_member", False):
                        channel_name = channel.get("name", channel_id)
                        await self._join_channel(user_token, channel_id, channel_name)

                    # Fetch messages just to collect user IDs
                    messages = await self._fetch_channel_history(
                        bot_token=user_token,
                        channel_id=channel_id,
                        limit=1000
                    )

                    # Collect user IDs
                    for msg in messages:
                        if msg.get("user"):
                            all_user_ids.add(msg.get("user"))

                    await asyncio.sleep(0.5)  # Rate limiting

                # Sync all users at once
                logger.info("syncing_users", team_id=team_id, user_count=len(all_user_ids))
                synced_count = 0
                failed_count = 0

                from app.services.sync_service import sync_service
                from app.services.slack_client import slack_client_manager

                # Initialize the Slack client with the workspace's user token
                slack_client_manager.client.token = user_token

                for user_id in all_user_ids:
                    try:
                        # Fetch user data from Slack API
                        user_data = await slack_client_manager.get_user_info(user_id)

                        if not user_data:
                            logger.warning("user_info_unavailable", user_id=user_id)
                            failed_count += 1
                            continue

                        # Check if user exists
                        from app.models.user import User
                        result = await db.execute(
                            select(User).where(User.user_id == user_id)
                        )
                        existing = result.scalar_one_or_none()

                        profile = user_data.get("profile", {})

                        if existing:
                            # Update existing
                            existing.username = user_data.get("name")
                            existing.real_name = user_data.get("real_name")
                            existing.display_name = profile.get("display_name")
                            existing.email = profile.get("email")
                            existing.is_admin = 1 if user_data.get("is_admin") else 0
                            existing.is_owner = 1 if user_data.get("is_owner") else 0
                            existing.is_deleted = 1 if user_data.get("deleted") else 0
                            existing.title = profile.get("title")
                            existing.timezone = user_data.get("tz")
                            existing.avatar_url = profile.get("image_512")
                            existing.last_active_at = datetime.utcnow()
                        else:
                            # Create new
                            user = User(
                                user_id=user_id,
                                team_id=team_id,
                                username=user_data.get("name"),
                                real_name=user_data.get("real_name"),
                                display_name=profile.get("display_name"),
                                email=profile.get("email"),
                                is_bot=1 if user_data.get("is_bot") else 0,
                                is_admin=1 if user_data.get("is_admin") else 0,
                                is_owner=1 if user_data.get("is_owner") else 0,
                                is_deleted=1 if user_data.get("deleted") else 0,
                                title=profile.get("title"),
                                timezone=user_data.get("tz"),
                                avatar_url=profile.get("image_512"),
                                slack_created_at=datetime.fromtimestamp(user_data.get("updated", 0))
                            )
                            db.add(user)

                        synced_count += 1

                    except Exception as e:
                        logger.warning("user_sync_failed", user_id=user_id, error=str(e))
                        failed_count += 1

                    # Rate limiting to avoid API throttling
                    await asyncio.sleep(0.1)

                # Commit all user syncs at once
                await db.commit()

                logger.info(
                    "users_synced",
                    team_id=team_id,
                    synced=synced_count,
                    failed=failed_count,
                    total=len(all_user_ids)
                )

                # Step 3: Update total items estimate
                # Estimate: average 500 messages per indexable channel
                indexable_channels = sum(
                    1 for ch in channels
                    if not ch.get("is_archived", False)  # Simple estimate
                )
                estimated_total = indexable_channels * 500
                integration_status.total_items = estimated_total
                await db.commit()

                # Step 4: Index messages from each channel with checkpointing
                total_messages = messages_already_indexed
                indexed_channels = 0
                should_skip = resume_from_channel is not None  # Skip until we reach checkpoint channel

                for channel in channels:
                    channel_id = channel["id"]
                    is_private = channel.get("is_private", False)
                    is_archived = channel.get("is_archived", False)
                    is_dm = channel.get("is_im", False) or channel_id.startswith("D")
                    is_mpim = channel.get("is_mpim", False)

                    # Get channel name (handle DMs which don't have 'name' field)
                    if is_dm:
                        user_id = channel.get("user", "unknown")
                        channel_name = f"dm-{user_id}"
                    else:
                        channel_name = channel.get("name", channel_id)

                    # If resuming from checkpoint, skip channels until we reach the checkpoint
                    if should_skip:
                        if channel_id == resume_from_channel:
                            should_skip = False
                            logger.info(
                                "resuming_from_checkpoint",
                                team_id=team_id,
                                channel_id=channel_id,
                                channel_name=channel_name
                            )
                            continue  # Skip the checkpoint channel (already indexed)
                        else:
                            continue  # Skip this channel

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

                    # Join channel if public (skip DMs and MPIMs - already have access)
                    if not is_private and not is_dm and not is_mpim and not channel.get("is_member", False):
                        await self._join_channel(user_token, channel_id, channel_name)

                    # Fetch and index messages
                    messages = await self._fetch_channel_history(
                        bot_token=user_token,
                        channel_id=channel_id,
                        limit=1000  # Limit per channel for initial indexing
                    )

                    if messages:
                        # Index messages with progress tracking
                        await self._index_messages_with_checkpoints(
                            messages=messages,
                            channel_id=channel_id,
                            channel_name=channel_name,
                            team_id=team_id,
                            integration_status=integration_status,
                            db=db
                        )

                        total_messages += len(messages)
                        indexed_channels += 1

                        # Save checkpoint after each channel
                        integration_status.last_checkpoint = {
                            "last_channel_id": channel_id,
                            "timestamp": datetime.utcnow().isoformat()
                        }
                        integration_status.messages_indexed = total_messages
                        integration_status.items_indexed = total_messages
                        integration_status.progress_percentage = min(
                            100.0,
                            (total_messages / estimated_total * 100) if estimated_total > 0 else 0
                        )
                        await db.commit()

                        logger.info(
                            "channel_indexed_checkpoint_saved",
                            team_id=team_id,
                            channel=channel_name,
                            messages=len(messages),
                            total_messages=total_messages,
                            progress=integration_status.progress_percentage
                        )

                    # Rate limiting - pause between channels
                    await asyncio.sleep(1)

                # Step 5: Build knowledge graph - entity relationships
                logger.info("building_entity_relationships", team_id=team_id)
                relationship_count = await self._build_entity_relationships(team_id, db)

                # Step 6: Calculate user expertise
                logger.info("calculating_user_expertise", team_id=team_id)
                expertise_count = await self._calculate_user_expertise(team_id, db)

                # Step 7: Detect significant events
                logger.info("detecting_significant_events", team_id=team_id)
                event_count = await self._detect_significant_events(team_id, db)

                # Step 8: Update integration status to complete
                integration_status.indexing_status = "complete"
                integration_status.indexing_completed_at = datetime.utcnow()
                integration_status.messages_indexed = total_messages
                integration_status.items_indexed = total_messages
                integration_status.entities_extracted = relationship_count
                integration_status.progress_percentage = 100.0
                integration_status.last_full_sync = datetime.utcnow()
                integration_status.last_checkpoint = None  # Clear checkpoint on completion
                await db.commit()

                # Step 9: Update workspace indexing status (backward compatibility)
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

                # Step 10: Notify orchestrator about completion
                try:
                    from app.services.integration_orchestrator import get_integration_orchestrator
                    orchestrator = get_integration_orchestrator(db)

                    await orchestrator.handle_indexing_complete(
                        team_id=team_id,
                        source_type="slack",
                        result={
                            "success": True,
                            "messages_indexed": total_messages,
                            "channels_indexed": indexed_channels,
                            "relationships_built": relationship_count,
                            "expertise_areas": expertise_count,
                            "events_detected": event_count
                        }
                    )

                    logger.info(
                        "orchestrator_notified",
                        team_id=team_id,
                        source_type="slack"
                    )

                except Exception as e:
                    logger.error(
                        "orchestrator_notification_failed",
                        error=str(e),
                        team_id=team_id
                    )

            except Exception as e:
                logger.error(
                    "initial_indexing_error",
                    error=str(e),
                    team_id=team_id
                )

                # Update integration status to failed (checkpoint preserved for resume)
                try:
                    stmt = select(IntegrationStatus).where(
                        and_(
                            IntegrationStatus.team_id == team_id,
                            IntegrationStatus.source_type == "slack"
                        )
                    )
                    result = await db.execute(stmt)
                    failed_status = result.scalar_one_or_none()

                    if failed_status:
                        failed_status.indexing_status = "failed"
                        failed_status.indexing_failed_at = datetime.utcnow()
                        failed_status.indexing_error = str(e)
                        # Keep last_checkpoint for resume
                        await db.commit()
                        logger.info(
                            "integration_status_marked_failed",
                            team_id=team_id,
                            checkpoint_preserved=failed_status.last_checkpoint is not None
                        )
                except Exception as db_error:
                    logger.error("failed_to_update_integration_status", error=str(db_error), team_id=team_id)

                # Update workspace status to failed (backward compatibility)
                try:
                    stmt = select(Workspace).where(Workspace.team_id == team_id)
                    result = await db.execute(stmt)
                    workspace_to_fail = result.scalar_one_or_none()

                    if workspace_to_fail:
                        workspace_to_fail.indexing_status = "failed"
                        await db.commit()
                        logger.info("workspace_marked_as_failed", team_id=team_id)
                except Exception as db_error:
                    logger.error("failed_to_update_workspace_status", error=str(db_error), team_id=team_id)

                raise

    async def _fetch_channels(
        self,
        bot_token: str,
        team_id: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch all conversations from Slack (Glean-style complete workspace indexing)

        Includes:
        - public_channel: Public channels
        - private_channel: Private channels (if user has access)
        - im: Direct messages (DMs)
        - mpim: Multi-party instant messages (group DMs)
        """

        channels = []

        async with httpx.AsyncClient() as client:
            cursor = None

            while True:
                params = {
                    "types": "public_channel,private_channel,im,mpim",
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

    async def _fetch_selected_channels(
        self,
        bot_token: str,
        team_id: str,
        selected_channel_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Fetch only selected channels from Slack using conversations.info"""

        channels = []

        async with httpx.AsyncClient() as client:
            for channel_id in selected_channel_ids:
                try:
                    response = await client.post(
                        "https://slack.com/api/conversations.info",
                        headers={"Authorization": f"Bearer {bot_token}"},
                        data={"channel": channel_id}
                    )

                    data = response.json()

                    if data.get("ok"):
                        channels.append(data.get("channel"))
                    else:
                        logger.warning(
                            "selected_channel_not_accessible",
                            channel_id=channel_id,
                            team_id=team_id,
                            error=data.get("error")
                        )

                except Exception as e:
                    logger.error(
                        "fetch_selected_channel_error",
                        channel_id=channel_id,
                        team_id=team_id,
                        error=str(e)
                    )

        logger.info(
            "selected_channels_fetched",
            team_id=team_id,
            requested=len(selected_channel_ids),
            found=len(channels)
        )

        return channels

    async def _join_channel(
        self,
        bot_token: str,
        channel_id: str,
        channel_name: str
    ) -> bool:
        """Join a public channel"""

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://slack.com/api/conversations.join",
                headers={"Authorization": f"Bearer {bot_token}"},
                data={"channel": channel_id}
            )

            data = response.json()

            if data.get("ok"):
                logger.info(
                    "channel_joined",
                    channel_id=channel_id,
                    channel_name=channel_name
                )
                return True
            else:
                logger.warning(
                    "channel_join_failed",
                    channel_id=channel_id,
                    channel_name=channel_name,
                    error=data.get("error")
                )
                return False

    async def _store_channels(
        self,
        channels: List[Dict[str, Any]],
        team_id: str,
        db
    ) -> None:
        """
        Store channel information in database

        Handles all conversation types:
        - Channels: Use 'name' field
        - DMs (im): Use 'user' field (user ID) as name
        - MPIMs: Use 'name' field (auto-generated by Slack)
        """

        for channel_data in channels:
            # Determine channel name based on conversation type
            is_dm = channel_data.get("is_im", False) or channel_data["id"].startswith("D")

            if is_dm:
                # For DMs, use user ID as name (format: dm-USER_ID)
                user_id = channel_data.get("user", "unknown")
                channel_name = f"dm-{user_id}"
            else:
                # For channels and MPIMs, use the 'name' field
                channel_name = channel_data.get("name", f"unknown-{channel_data['id']}")

            # Check if channel exists
            stmt = select(Channel).where(Channel.channel_id == channel_data["id"])
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                # Update existing channel
                existing.channel_name = channel_name
                existing.is_private = 1 if channel_data.get("is_private") or is_dm else 0
                existing.is_archived = 1 if channel_data.get("is_archived") else 0
                existing.member_count = channel_data.get("num_members", 0)
                existing.updated_at = datetime.utcnow()
            else:
                # Create new channel
                channel = Channel(
                    channel_id=channel_data["id"],
                    channel_name=channel_name,
                    team_id=team_id,
                    is_private=1 if channel_data.get("is_private") or is_dm else 0,
                    is_archived=1 if channel_data.get("is_archived") else 0,
                    member_count=channel_data.get("num_members", 0),
                    topic=channel_data.get("topic", {}).get("value") if not is_dm else None,
                    purpose=channel_data.get("purpose", {}).get("value") if not is_dm else None,
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

            # Skip messages from the bot itself (by user_id or bot_id)
            from app.core.config import settings
            if msg_data.get("user") == settings.slack_bot_user_id:
                logger.debug("skipping_bot_user_message", user_id=msg_data.get("user"))
                continue

            if msg_data.get("bot_id"):
                logger.debug("skipping_bot_id_message", bot_id=msg_data.get("bot_id"))
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

    async def _index_messages_with_checkpoints(
        self,
        messages: List[Dict[str, Any]],
        channel_id: str,
        channel_name: str,
        team_id: str,
        integration_status: IntegrationStatus,
        db
    ) -> None:
        """Process and index messages with periodic checkpointing"""

        message_count = 0
        checkpoint_counter = 0

        for msg_data in messages:
            # Skip bot messages and certain subtypes
            if msg_data.get("subtype") in ["bot_message", "channel_join", "channel_leave"]:
                continue

            # Skip messages from the bot itself (by user_id or bot_id)
            from app.core.config import settings
            if msg_data.get("user") == settings.slack_bot_user_id:
                logger.debug("skipping_bot_user_message", user_id=msg_data.get("user"))
                continue

            if msg_data.get("bot_id"):
                logger.debug("skipping_bot_id_message", bot_id=msg_data.get("bot_id"))
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

            message_count += 1
            checkpoint_counter += 1

            # Save checkpoint every CHECKPOINT_INTERVAL messages
            if checkpoint_counter >= CHECKPOINT_INTERVAL:
                integration_status.messages_indexed += checkpoint_counter
                integration_status.items_indexed = integration_status.messages_indexed

                # Update progress
                if integration_status.total_items > 0:
                    integration_status.progress_percentage = min(
                        100.0,
                        (integration_status.messages_indexed / integration_status.total_items) * 100
                    )

                await db.commit()

                logger.debug(
                    "checkpoint_saved_within_channel",
                    team_id=team_id,
                    channel_id=channel_id,
                    messages_processed=checkpoint_counter,
                    total_indexed=integration_status.messages_indexed
                )

                checkpoint_counter = 0

        # Update final count if there are remaining messages
        if checkpoint_counter > 0:
            integration_status.messages_indexed += checkpoint_counter
            integration_status.items_indexed = integration_status.messages_indexed
            await db.commit()

    async def _build_entity_relationships(self, team_id: str, db) -> int:
        """Build entity relationships from co-occurrence patterns in unified graph"""

        try:
            from app.models.cross_source_node import CrossSourceNode
            from app.models.cross_source_edge import CrossSourceEdge, EdgeType, DetectionMethod
            from sqlalchemy import and_, func
            from datetime import datetime

            # Get all entity nodes for this workspace from unified graph
            result = await db.execute(
                select(CrossSourceNode).where(
                    and_(
                        CrossSourceNode.team_id == team_id,
                        CrossSourceNode.source == "derived",  # Entities are derived
                        CrossSourceNode.canonical_id.like("entity:%")  # Entity canonical_id format
                    )
                )
            )
            entities = result.scalars().all()

            if len(entities) < 2:
                logger.info("not_enough_entities_for_relationships", team_id=team_id, count=len(entities))
                return 0

            relationship_count = 0

            # For each pair of entities, check if they co-occur in messages
            for i, entity1 in enumerate(entities):
                for entity2 in entities[i+1:]:
                    # Extract entity text from metadata for searching
                    entity1_text = entity1.entity_metadata.get("canonical_form", entity1.title)
                    entity2_text = entity2.entity_metadata.get("canonical_form", entity2.title)

                    # Check messages containing both entities
                    from app.models.message import Message
                    result = await db.execute(
                        select(func.count(Message.id)).where(
                            and_(
                                Message.team_id == team_id,
                                Message.text_processed.ilike(f"%{entity1_text}%"),
                                Message.text_processed.ilike(f"%{entity2_text}%")
                            )
                        )
                    )
                    co_occurrence_count = result.scalar()

                    if co_occurrence_count > 0:
                        # Calculate confidence score
                        entity1_count = entity1.entity_metadata.get("occurrence_count", 1)
                        entity2_count = entity2.entity_metadata.get("occurrence_count", 1)
                        confidence = min(1.0, co_occurrence_count / min(entity1_count, entity2_count))

                        # Check if edge already exists (bidirectional check)
                        result = await db.execute(
                            select(CrossSourceEdge).where(
                                and_(
                                    CrossSourceEdge.team_id == team_id,
                                    CrossSourceEdge.edge_type == EdgeType.CO_OCCURS_WITH,
                                    or_(
                                        and_(
                                            CrossSourceEdge.source_node_id == entity1.canonical_id,
                                            CrossSourceEdge.target_node_id == entity2.canonical_id
                                        ),
                                        and_(
                                            CrossSourceEdge.source_node_id == entity2.canonical_id,
                                            CrossSourceEdge.target_node_id == entity1.canonical_id
                                        )
                                    )
                                )
                            )
                        )
                        existing = result.scalar_one_or_none()

                        if not existing:
                            # Create edge in unified graph
                            import uuid
                            edge = CrossSourceEdge(
                                id=str(uuid.uuid4()),
                                source_node_id=entity1.canonical_id,
                                target_node_id=entity2.canonical_id,
                                team_id=team_id,
                                edge_type=EdgeType.CO_OCCURS_WITH,
                                detection_method=DetectionMethod.CO_OCCURRENCE,
                                confidence=round(confidence, 4),
                                edge_metadata={
                                    "co_occurrence_count": co_occurrence_count,
                                    "confidence_score": round(confidence, 4)
                                },
                                created_at=datetime.utcnow(),
                                updated_at=datetime.utcnow()
                            )
                            db.add(edge)
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
