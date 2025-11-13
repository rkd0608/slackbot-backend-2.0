"""Workspace management API endpoints"""
from fastapi import APIRouter, HTTPException, Depends, Query, Body
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime
from typing import List, Optional
import httpx
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.models.workspace import Workspace
from app.models.channel import Channel
from app.core.logging import get_logger
from app.services.workspace_deletion_service import workspace_deletion_service

logger = get_logger(__name__)
router = APIRouter()


class ChannelInfo(BaseModel):
    """Channel information for selection UI"""
    channel_id: str
    name: str
    is_private: bool
    is_archived: bool
    member_count: int
    topic: Optional[str] = None
    purpose: Optional[str] = None
    is_member: bool
    indexing_enabled: bool
    indexing_status: str


class ChannelListResponse(BaseModel):
    """Response for channel list"""
    team_id: str
    team_name: str
    channels: List[ChannelInfo]
    total_channels: int
    public_channels: int
    private_channels: int


class ConfigureChannelsRequest(BaseModel):
    """Request to configure which channels to index"""
    channel_ids: List[str]
    start_indexing: bool = True


class ConfigureChannelsResponse(BaseModel):
    """Response after channel configuration"""
    success: bool
    message: str
    team_id: str
    channels_configured: int
    channels_enabled: List[str]
    indexing_status: str
    next_step: dict


class WorkspaceStatusResponse(BaseModel):
    """Workspace status including indexing progress"""
    team_id: str
    team_name: str
    subscription: dict
    indexing: dict
    usage: dict
    is_ready: bool


class DeleteWorkspaceRequest(BaseModel):
    """Request to delete workspace"""
    confirmation_text: str  # Must match workspace name
    delete_from_slack: bool = True  # Whether to uninstall from Slack


class DeleteWorkspaceResponse(BaseModel):
    """Response after workspace deletion"""
    success: bool
    message: str
    team_id: str
    workspace_name: str
    deletion_stats: dict


@router.get("/workspaces/{team_id}/channels", response_model=ChannelListResponse)
async def get_workspace_channels(
    team_id: str,
    include_archived: bool = Query(False, description="Include archived channels"),
    types: str = Query("public,private,dm,group", description="Conversation types: public,private,dm,group"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get list of all conversations in workspace for selection (Glean-style)

    Returns all conversation types:
    - public: Public channels
    - private: Private channels
    - dm: Direct messages
    - group: Group messages (MPIMs)
    """

    try:
        # Verify user has access to this workspace
        if current_user.team_id != team_id:
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this workspace"
            )

        # Get workspace
        stmt = select(Workspace).where(Workspace.team_id == team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise HTTPException(
                status_code=404,
                detail="Workspace not found"
            )

        # Get bot token
        bot_token = workspace.bot_access_token

        # Fetch channels from Slack API
        channels_data = await _fetch_slack_channels(
            bot_token=bot_token,
            include_archived=include_archived,
            types=types
        )

        # Get existing channel records from DB
        stmt = select(Channel).where(Channel.team_id == team_id)
        result = await db.execute(stmt)
        existing_channels = {ch.channel_id: ch for ch in result.scalars().all()}

        # Build response
        channels = []
        public_count = 0
        private_count = 0

        for slack_channel in channels_data:
            channel_id = slack_channel["id"]
            is_private = slack_channel.get("is_private", False)
            is_dm = slack_channel.get("is_im", False) or channel_id.startswith("D")

            # Determine channel name (handle DMs which don't have 'name' field)
            if is_dm:
                user_id = slack_channel.get("user", "unknown")
                channel_name = f"DM with {user_id}"
            else:
                channel_name = slack_channel.get("name", f"unknown-{channel_id}")

            if is_private or is_dm:
                private_count += 1
            else:
                public_count += 1

            # Get DB record if exists
            db_channel = existing_channels.get(channel_id)

            channels.append(ChannelInfo(
                channel_id=channel_id,
                name=channel_name,
                is_private=is_private or is_dm,
                is_archived=slack_channel.get("is_archived", False),
                member_count=slack_channel.get("num_members", 0),
                topic=slack_channel.get("topic", {}).get("value") if not is_dm else None,
                purpose=slack_channel.get("purpose", {}).get("value") if not is_dm else None,
                is_member=slack_channel.get("is_member", True),
                indexing_enabled=db_channel.indexing_enabled if db_channel else True,
                indexing_status=db_channel.indexing_status if db_channel else "pending"
            ))

        logger.info(
            "channels_listed",
            team_id=team_id,
            total=len(channels),
            public=public_count,
            private=private_count
        )

        return ChannelListResponse(
            team_id=team_id,
            team_name=workspace.team_name,
            channels=channels,
            total_channels=len(channels),
            public_channels=public_count,
            private_channels=private_count
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_channels_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch channels"
        )


@router.post("/workspaces/{team_id}/channels/configure", response_model=ConfigureChannelsResponse)
async def configure_channels(
    team_id: str,
    request: ConfigureChannelsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Configure which channels should be indexed

    Updates channel indexing preferences and optionally starts indexing
    """

    try:
        # Verify user has access and is admin
        if current_user.team_id != team_id:
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this workspace"
            )

        if not current_user.is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only workspace admins can configure channels"
            )

        # Get workspace
        stmt = select(Workspace).where(Workspace.team_id == team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise HTTPException(
                status_code=404,
                detail="Workspace not found"
            )

        # Get all channels for this workspace
        stmt = select(Channel).where(Channel.team_id == team_id)
        result = await db.execute(stmt)
        channels = {ch.channel_id: ch for ch in result.scalars().all()}

        # Update indexing_enabled for each channel
        for channel_id in channels.keys():
            channel = channels[channel_id]

            if channel_id in request.channel_ids:
                # Enable indexing
                channel.indexing_enabled = 1
                if channel.indexing_status == "pending":
                    channel.indexing_status = "pending"
            else:
                # Disable indexing
                channel.indexing_enabled = 0

        await db.commit()

        # If start_indexing is true, trigger indexing (allows re-indexing)
        if request.start_indexing:
            # Track if this is a re-index before changing status
            is_reindex = workspace.indexing_status == "complete"

            # Reset and update workspace status for (re)indexing
            workspace.indexing_status = "in_progress"
            workspace.indexing_started_at = datetime.utcnow()
            workspace.indexing_completed_at = None
            workspace.total_messages_indexed = 0
            workspace.total_channels_indexed = 0
            await db.commit()

            # Trigger indexing job
            from app.core.queue import queue_manager

            publish_success = await queue_manager.publish(
                queue=queue_manager.PROCESSING_QUEUE,
                message={
                    "type": "initial_workspace_indexing",
                    "team_id": team_id,
                    "workspace_id": workspace.id,
                    "bot_token": workspace.bot_access_token,
                    "channel_ids": request.channel_ids  # Only index selected channels
                },
                routing_key="workspace.indexing.initial"
            )

            logger.info(
                "indexing_triggered",
                team_id=team_id,
                channels=len(request.channel_ids),
                is_reindex=is_reindex,
                publish_success=publish_success,
                queue_initialized=queue_manager._initialized
            )

        logger.info(
            "channels_configured",
            team_id=team_id,
            enabled_count=len(request.channel_ids),
            start_indexing=request.start_indexing
        )

        return ConfigureChannelsResponse(
            success=True,
            message="Channel configuration saved successfully",
            team_id=team_id,
            channels_configured=len(request.channel_ids),
            channels_enabled=request.channel_ids,
            indexing_status=workspace.indexing_status,
            next_step={
                "action": "poll_status",
                "url": f"/api/v1/workspaces/{team_id}/status"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("configure_channels_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=500,
            detail="Failed to configure channels"
        )


@router.get("/workspaces/{team_id}/status", response_model=WorkspaceStatusResponse)
async def get_workspace_status(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get workspace status including indexing progress

    Poll this endpoint to check indexing status
    """

    try:
        # Verify user has access
        if current_user.team_id != team_id:
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this workspace"
            )

        # Get workspace
        stmt = select(Workspace).where(Workspace.team_id == team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise HTTPException(
                status_code=404,
                detail="Workspace not found"
            )

        # Calculate indexing progress
        indexing_data = {
            "status": workspace.indexing_status,
            "started_at": workspace.indexing_started_at.isoformat() if workspace.indexing_started_at else None,
            "completed_at": workspace.indexing_completed_at.isoformat() if workspace.indexing_completed_at else None,
            "channels_indexed": workspace.total_channels_indexed,
            "messages_indexed": workspace.total_messages_indexed
        }

        # Calculate progress percentage
        if workspace.indexing_status == "pending":
            indexing_data["progress_percentage"] = 0
        elif workspace.indexing_status == "complete":
            indexing_data["progress_percentage"] = 100
        elif workspace.indexing_status == "in_progress":
            # Get total channels to index
            stmt = select(Channel).where(
                and_(
                    Channel.team_id == team_id,
                    Channel.indexing_enabled == 1
                )
            )
            result = await db.execute(stmt)
            total_channels = len(result.scalars().all())

            if total_channels > 0:
                progress = (workspace.total_channels_indexed / total_channels) * 100
                indexing_data["progress_percentage"] = min(95, int(progress))  # Cap at 95% until complete
            else:
                indexing_data["progress_percentage"] = 50  # Default midpoint

            indexing_data["channels_total"] = total_channels
        else:
            indexing_data["progress_percentage"] = 0

        # Subscription data
        subscription_data = {
            "status": workspace.subscription_status,
            "tier": workspace.subscription_tier,
            "trial_days_remaining": workspace.trial_days_remaining,
            "trial_ends_at": workspace.trial_ends_at.isoformat() if workspace.trial_ends_at else None
        }

        # Usage data
        usage_data = {
            "queries_used_this_month": workspace.queries_used_this_month,
            "queries_limit": workspace.monthly_query_limit,
            "queries_remaining": workspace.monthly_query_limit - workspace.queries_used_this_month
        }

        # Determine if workspace is ready to use
        is_ready = workspace.indexing_status == "complete" and workspace.is_subscription_active

        return WorkspaceStatusResponse(
            team_id=team_id,
            team_name=workspace.team_name,
            subscription=subscription_data,
            indexing=indexing_data,
            usage=usage_data,
            is_ready=is_ready
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_status_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=500,
            detail="Failed to get workspace status"
        )


async def _fetch_slack_channels(
    bot_token: str,
    include_archived: bool = False,
    types: str = "public_channel,private_channel,im,mpim"
) -> List[dict]:
    """
    Fetch conversations from Slack API (Glean-style)

    Supports all conversation types:
    - public_channel: Public channels
    - private_channel: Private channels
    - im: Direct messages
    - mpim: Multi-party instant messages (group DMs)
    """

    channels = []

    async with httpx.AsyncClient() as client:
        cursor = None

        while True:
            # Convert short types to Slack API format
            slack_types = (types
                .replace("public", "public_channel")
                .replace("private", "private_channel")
                .replace("dm", "im")
                .replace("group", "mpim"))

            params = {
                "types": slack_types,
                "exclude_archived": not include_archived,
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
                logger.error("slack_api_error", error=data.get("error"))
                raise Exception(f"Slack API error: {data.get('error')}")

            channels.extend(data.get("channels", []))

            # Check for pagination
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    return channels


@router.delete("/workspaces/{team_id}", response_model=DeleteWorkspaceResponse)
async def delete_workspace(
    team_id: str,
    request: DeleteWorkspaceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete workspace and all associated data

    **DANGER: This action is irreversible!**

    This endpoint will:
    - Delete all database records (messages, threads, channels, users, etc.)
    - Delete all vector embeddings from Pinecone
    - Optionally uninstall the app from Slack
    - Remove all integration data (GitHub, Jira, etc.)

    **Authorization**: Only workspace admins can delete the workspace

    **Confirmation**: Must provide workspace name as confirmation
    """

    try:
        # Step 1: Get workspace to check name and permissions
        stmt = select(Workspace).where(Workspace.team_id == team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise HTTPException(
                status_code=404,
                detail="Workspace not found"
            )

        # Step 2: Verify user has access to this workspace
        if current_user.team_id != team_id:
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this workspace"
            )

        # Step 3: Verify user is workspace admin
        if not current_user.is_workspace_admin:
            raise HTTPException(
                status_code=403,
                detail="Only workspace administrators can delete the workspace"
            )

        # Step 4: Verify confirmation text matches workspace name
        if request.confirmation_text != workspace.team_name:
            raise HTTPException(
                status_code=400,
                detail=f"Confirmation text must exactly match workspace name: '{workspace.team_name}'"
            )

        # Step 5: Perform deletion
        logger.warning(
            "workspace_deletion_initiated",
            team_id=team_id,
            workspace_name=workspace.team_name,
            admin_user_id=current_user.user_id,
            delete_from_slack=request.delete_from_slack
        )

        deletion_result = await workspace_deletion_service.delete_workspace(
            team_id=team_id,
            db=db,
            delete_from_slack=request.delete_from_slack,
            admin_user_id=current_user.user_id
        )

        if not deletion_result["success"]:
            raise HTTPException(
                status_code=500,
                detail=f"Workspace deletion failed: {deletion_result.get('errors', [])}"
            )

        # Step 6: Delete workspace record itself (after logging)
        from app.services.workspace_deletion_service import WorkspaceDeletionService
        deletion_svc = WorkspaceDeletionService()
        await deletion_svc._delete_workspace_record(team_id, db)
        await db.commit()

        logger.warning(
            "workspace_deletion_completed",
            team_id=team_id,
            workspace_name=workspace.team_name,
            admin_user_id=current_user.user_id,
            stats=deletion_result["deleted"]
        )

        return DeleteWorkspaceResponse(
            success=True,
            message=f"Workspace '{workspace.team_name}' has been permanently deleted",
            team_id=team_id,
            workspace_name=workspace.team_name,
            deletion_stats=deletion_result["deleted"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "delete_workspace_error",
            error=str(e),
            team_id=team_id
        )
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete workspace: {str(e)}"
        )