"""Onboarding API endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime
from typing import Optional, List, Dict, Any
from app.core.database import get_db
from app.core.logging import get_logger
from app.services.auth_service import auth_service
from app.core.cache import cache_manager
from app.core.auth import get_current_user
from app.models.user import User
from app.models.workspace import Workspace

logger = get_logger(__name__)
router = APIRouter()


class SignupRequest(BaseModel):
    """User signup request before Slack OAuth"""
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    company_name: str
    team_size: str  # e.g., "1-10", "10-50", "50-200", "200+"

    @validator('first_name', 'last_name')
    def validate_name(cls, v):
        if not v or len(v.strip()) < 2:
            raise ValueError('Name must be at least 2 characters')
        return v.strip()

    @validator('password')
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        return v

    @validator('team_size')
    def validate_team_size(cls, v):
        valid_sizes = ['1-10', '10-50', '50-200', '200-500', '500+']
        if v not in valid_sizes:
            raise ValueError(f'Team size must be one of: {", ".join(valid_sizes)}')
        return v


class SignupResponse(BaseModel):
    """Signup response with onboarding token"""
    success: bool
    message: str
    user: dict
    onboarding_token: str
    next_step: dict


@router.post("/onboarding/signup", response_model=SignupResponse)
async def signup(
    request: SignupRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Create new user account before Slack OAuth

    This creates a pending user record that will be linked to a Slack user
    after OAuth completes.
    """

    try:
        # Check if email already exists in pending signups
        existing_signup = await cache_manager.get(f"pending_signup:{request.email}")
        if existing_signup:
            raise HTTPException(
                status_code=400,
                detail="Email already registered. Please complete Slack OAuth or use a different email."
            )

        # Hash password
        password_hash = auth_service.hash_password(request.password)

        # Generate unique temporary ID
        import secrets
        temp_user_id = f"temp_{secrets.token_urlsafe(16)}"

        # Store pending signup in Redis (expires in 1 hour)
        pending_user_data = {
            "temp_user_id": temp_user_id,
            "first_name": request.first_name,
            "last_name": request.last_name,
            "email": request.email,
            "password_hash": password_hash,
            "company_name": request.company_name,
            "team_size": request.team_size,
            "created_at": datetime.utcnow().isoformat()
        }

        await cache_manager.set(
            f"pending_signup:{request.email}",
            pending_user_data,
            ttl=3600  # 1 hour
        )

        # Also store by temp_user_id for reverse lookup
        await cache_manager.set(
            f"pending_signup_by_id:{temp_user_id}",
            pending_user_data,
            ttl=3600
        )

        # Create onboarding token (JWT) with temp user data
        token_data = {
            "temp_user_id": temp_user_id,
            "email": request.email,
            "type": "onboarding"
        }
        onboarding_token = auth_service.create_access_token(token_data)

        logger.info(
            "user_signup_initiated",
            email=request.email,
            company=request.company_name,
            team_size=request.team_size
        )

        return SignupResponse(
            success=True,
            message="Account created successfully. Please connect your Slack workspace.",
            user={
                "id": temp_user_id,
                "email": request.email,
                "first_name": request.first_name,
                "last_name": request.last_name,
                "company_name": request.company_name,
                "team_size": request.team_size
            },
            onboarding_token=onboarding_token,
            next_step={
                "action": "slack_oauth",
                "url": f"/oauth/install?state={onboarding_token}"
            }
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("signup_error", error=str(e), email=request.email)
        raise HTTPException(
            status_code=500,
            detail="Signup failed. Please try again."
        )


@router.get("/onboarding/verify-email/{token}")
async def verify_email_token(token: str):
    """
    Verify that an onboarding token is still valid

    Frontend can use this to check if user needs to re-signup
    """

    try:
        payload = auth_service.verify_token(token)

        if not payload or payload.get("type") != "onboarding":
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired onboarding token"
            )

        email = payload.get("email")
        pending_signup = await cache_manager.get(f"pending_signup:{email}")

        if not pending_signup:
            raise HTTPException(
                status_code=404,
                detail="Signup expired. Please sign up again."
            )

        return {
            "valid": True,
            "email": email,
            "expires_in_seconds": 3600  # Approximate
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("verify_token_error", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Token verification failed"
        )


class CompleteProfileRequest(BaseModel):
    """Complete user profile after Slack OAuth"""
    team_id: str
    first_name: str
    last_name: str
    password: str
    company_name: str
    company_size: str  # e.g., "1-10", "11-50", "51-200", "201-500", "501+"

    @validator('first_name', 'last_name')
    def validate_name(cls, v):
        if not v or len(v.strip()) < 2:
            raise ValueError('Name must be at least 2 characters')
        return v.strip()

    @validator('password')
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        return v

    @validator('company_size')
    def validate_company_size(cls, v):
        valid_sizes = ['1-10', '11-50', '51-200', '201-500', '501+']
        if v not in valid_sizes:
            raise ValueError(f'Company size must be one of: {", ".join(valid_sizes)}')
        return v


class CompleteProfileResponse(BaseModel):
    """Profile completion response"""
    success: bool
    message: str
    user: dict
    workspace: dict
    access_token: str
    refresh_token: str
    next_step: dict


@router.post("/onboarding/complete-profile", response_model=CompleteProfileResponse)
async def complete_profile(
    request: CompleteProfileRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Complete user profile after Slack OAuth

    This endpoint is called after OAuth completes to capture:
    - User's full name
    - Account password
    - Company information
    """

    try:
        # Get workspace
        stmt = select(Workspace).where(Workspace.team_id == request.team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise HTTPException(
                status_code=404,
                detail="Workspace not found. Please complete Slack OAuth first."
            )

        # Get installer user (should have been created during OAuth)
        stmt = select(User).where(
            User.team_id == request.team_id,
            User.user_id == workspace.installer_user_id
        )
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found. Please contact support."
            )

        # Check if profile already completed
        if user.profile_completed:
            raise HTTPException(
                status_code=400,
                detail="Profile already completed"
            )

        # Hash password
        password_hash = auth_service.hash_password(request.password)

        # Update user with profile info
        user.display_name = f"{request.first_name} {request.last_name}"
        user.real_name = f"{request.first_name} {request.last_name}"
        user.password_hash = password_hash
        user.profile_completed = 1
        user.role = "admin"
        user.is_admin = 1
        user.updated_at = datetime.utcnow()

        # Update workspace with company info
        workspace.company_name = request.company_name
        workspace.company_size = request.company_size
        workspace.updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(user)
        await db.refresh(workspace)

        # Generate JWT tokens for authentication
        token_data = {
            "sub": user.user_id,  # JWT standard: subject claim
            "user_id": user.user_id,
            "team_id": user.team_id,
            "email": user.email,
            "role": user.role
        }
        access_token = auth_service.create_access_token(token_data)
        refresh_token = auth_service.create_refresh_token(token_data)

        logger.info(
            "profile_completed",
            user_id=user.user_id,
            team_id=workspace.team_id,
            company=request.company_name
        )

        return CompleteProfileResponse(
            success=True,
            message="Profile completed successfully",
            user={
                "user_id": user.user_id,
                "email": user.email,
                "display_name": user.display_name,
                "role": user.role,
                "is_admin": user.is_admin
            },
            workspace={
                "team_id": workspace.team_id,
                "team_name": workspace.team_name,
                "company_name": workspace.company_name,
                "company_size": workspace.company_size,
                "subscription_status": workspace.subscription_status,
                "subscription_tier": workspace.subscription_tier
            },
            access_token=access_token,
            refresh_token=refresh_token,
            next_step={
                "action": "channel_selection",
                "url": f"/api/v1/onboarding/channels?team_id={workspace.team_id}",
                "description": "Select Slack channels to index"
            }
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("complete_profile_error", error=str(e), team_id=request.team_id)
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Profile completion failed. Please try again."
        )


class IntegrationChoiceRequest(BaseModel):
    """User's integration choice during onboarding"""
    team_id: str
    connect_github: bool


@router.get("/onboarding/integration-choice")
async def get_integration_choice_options(
    team_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get integration choice options for onboarding

    Returns available integrations and their descriptions
    """
    try:
        # Verify workspace exists
        stmt = select(Workspace).where(Workspace.team_id == team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        return {
            "team_id": team_id,
            "team_name": workspace.team_name,
            "integrations": [
                {
                    "type": "slack",
                    "name": "Slack",
                    "status": "connected",
                    "description": "Your Slack workspace is already connected",
                    "icon": "slack",
                    "required": True
                },
                {
                    "type": "github",
                    "name": "GitHub",
                    "status": "optional",
                    "description": "Connect GitHub to search code, PRs, and issues alongside Slack conversations",
                    "icon": "github",
                    "required": False,
                    "benefits": [
                        "Search code repositories",
                        "Link Slack discussions to GitHub PRs",
                        "Find who worked on what",
                        "Track code changes in context"
                    ]
                }
            ],
            "recommendation": {
                "connect_now": True,
                "reason": "Connecting GitHub now enables full cross-source intelligence from the start"
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_integration_options_error", error=str(e), team_id=team_id)
        raise HTTPException(
            status_code=500,
            detail="Failed to load integration options"
        )


@router.post("/onboarding/integration-choice")
async def submit_integration_choice(
    request: IntegrationChoiceRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Submit integration choice during onboarding

    If user chooses to connect GitHub:
    - Return GitHub OAuth URL
    - Frontend redirects to GitHub OAuth

    If user skips GitHub:
    - Move to next onboarding step
    - User can connect GitHub later from settings
    """
    try:
        # Verify workspace exists
        stmt = select(Workspace).where(Workspace.team_id == request.team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        if request.connect_github:
            # User wants to connect GitHub - return OAuth URL
            logger.info(
                "onboarding_github_chosen",
                team_id=request.team_id
            )

            return {
                "success": True,
                "action": "github_oauth",
                "message": "Redirecting to GitHub authorization...",
                "next_step": {
                    "action": "github_oauth",
                    "url": "/oauth/github/authorize",
                    "description": "Connect your GitHub account"
                }
            }
        else:
            # User skipped GitHub - move to dashboard
            logger.info(
                "onboarding_github_skipped",
                team_id=request.team_id
            )

            return {
                "success": True,
                "action": "complete",
                "message": "Setup complete! Your bot is indexing Slack history.",
                "next_step": {
                    "action": "dashboard",
                    "url": f"/dashboard?team_id={request.team_id}",
                    "description": "View your workspace dashboard"
                },
                "note": "You can connect GitHub anytime from Settings"
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "submit_integration_choice_error",
            error=str(e),
            team_id=request.team_id
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to process integration choice"
        )


class ChannelSelectionRequest(BaseModel):
    """Channel selection during onboarding"""
    team_id: str
    channel_ids: List[str]

    @validator('channel_ids')
    def validate_channels(cls, v):
        if not v or len(v) < 1:
            raise ValueError('Select at least one channel')
        return v


@router.get("/onboarding/channels")
async def get_channels_for_selection(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get all Slack channels for onboarding selection

    Returns channels with metadata for user to choose which to index
    """
    try:
        # 1. Get workspace
        stmt = select(Workspace).where(Workspace.team_id == team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # 2. Verify user belongs to this workspace
        if current_user.team_id != team_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # 3. Fetch ALL conversations via Slack API (Glean-style: channels, DMs, groups)
        # Use user token instead of bot token for complete visibility
        import httpx
        from app.services.workspace_service import workspace_service

        # Get installer's user token for maximum visibility (Glean-style)
        user_token = await workspace_service.get_installer_user_token(team_id, db)

        if not user_token:
            # Fallback to bot token if no user token available
            logger.warning(
                "no_user_token_for_onboarding",
                team_id=team_id,
                message="Using bot token instead - may have limited visibility"
            )
            user_token = workspace.bot_access_token

        channels = []
        cursor = None

        async with httpx.AsyncClient() as client:
            while True:
                params = {
                    "types": "public_channel,private_channel,im,mpim",
                    "exclude_archived": False,  # Include archived to get all conversations
                    "limit": 200
                }

                if cursor:
                    params["cursor"] = cursor

                response = await client.post(
                    "https://slack.com/api/conversations.list",
                    headers={"Authorization": f"Bearer {user_token}"},
                    data=params
                )

                data = response.json()

                if not data.get("ok"):
                    logger.error(
                        "fetch_channels_error",
                        error=data.get("error"),
                        team_id=team_id
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to fetch channels from Slack: {data.get('error')}"
                    )

                channels.extend(data.get("channels", []))

                # Check for pagination
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        # 4. Collect all user IDs from DMs to fetch their names
        user_ids_to_fetch = set()
        for ch in channels:
            is_dm = ch.get("is_im", False) or ch["id"].startswith("D")
            if is_dm:
                user_id = ch.get("user")
                if user_id:
                    user_ids_to_fetch.add(user_id)

        # 5. Fetch user information for all DM participants and identify bots
        user_names = {}
        bot_user_ids = set()  # Track which users are bots/apps

        async with httpx.AsyncClient() as client:
            for user_id in user_ids_to_fetch:
                try:
                    response = await client.post(
                        "https://slack.com/api/users.info",
                        headers={"Authorization": f"Bearer {user_token}"},
                        data={"user": user_id}
                    )
                    data = response.json()

                    if data.get("ok"):
                        user = data.get("user", {})

                        # Check if user is a bot or app
                        is_bot = user.get("is_bot", False)
                        is_app_user = user.get("is_app_user", False)
                        # Slackbot is a special case - not marked as bot but should be filtered
                        is_slackbot = user_id == "USLACKBOT"

                        if is_bot or is_app_user or is_slackbot:
                            bot_user_ids.add(user_id)
                            logger.debug(
                                "bot_user_identified",
                                user_id=user_id,
                                is_bot=is_bot,
                                is_app_user=is_app_user,
                                is_slackbot=is_slackbot
                            )

                        # Get real name or display name
                        real_name = user.get("real_name") or user.get("profile", {}).get("real_name")
                        display_name = user.get("profile", {}).get("display_name")
                        name = display_name or real_name or user_id
                        user_names[user_id] = name
                    else:
                        user_names[user_id] = user_id  # Fallback to ID
                except Exception as e:
                    logger.warning(
                        "user_info_fetch_failed",
                        user_id=user_id,
                        error=str(e)
                    )
                    user_names[user_id] = user_id  # Fallback to ID

        logger.info(
            "user_names_fetched",
            team_id=team_id,
            user_count=len(user_names),
            bot_count=len(bot_user_ids)
        )

        # 6. Format response (handle all conversation types, exclude bot DMs)
        formatted_channels = []
        for ch in channels:
            channel_id = ch["id"]
            is_dm = ch.get("is_im", False) or channel_id.startswith("D")
            is_mpim = ch.get("is_mpim", False)

            # Skip DMs with bots/apps (like Donut, Eunoia, Slackbot)
            if is_dm:
                user_id = ch.get("user", "unknown")
                if user_id in bot_user_ids:
                    logger.debug(
                        "bot_dm_filtered",
                        user_id=user_id,
                        channel_id=channel_id
                    )
                    continue  # Skip this DM

            # Handle naming for different conversation types
            if is_dm:
                # DMs: Use actual user name instead of ID
                user_id = ch.get("user", "unknown")
                user_name = user_names.get(user_id, user_id)
                name = f"DM with {user_name}"
            else:
                name = ch.get("name", f"Unnamed-{channel_id}")

            formatted_channels.append({
                "channel_id": channel_id,
                "name": name,
                "is_private": ch.get("is_private", False) or is_dm,
                "is_dm": is_dm,
                "is_group": is_mpim,
                "member_count": ch.get("num_members", 0),
                "topic": ch.get("topic", {}).get("value", "") if not is_dm else "",
                "purpose": ch.get("purpose", {}).get("value", "") if not is_dm else "",
                "is_member": ch.get("is_member", False),
                "pre_selected": True  # All selected by default
            })

        logger.info(
            "channels_fetched_for_selection",
            team_id=team_id,
            total_channels=len(formatted_channels)
        )

        return {
            "team_id": team_id,
            "team_name": workspace.team_name,
            "channels": formatted_channels,
            "total_channels": len(formatted_channels),
            "recommendation": "Select channels containing important work discussions"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "get_channels_error",
            error=str(e),
            team_id=team_id
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch channels"
        )


@router.post("/onboarding/channels/select")
async def select_channels_and_trigger_indexing(
    request: ChannelSelectionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Save selected channels and trigger Slack indexing

    This endpoint:
    1. Saves selected channel IDs to IntegrationStatus.config
    2. Creates IndexingJob with selected channel IDs
    3. Triggers background indexing via orchestrator
    4. Returns next onboarding step
    """
    try:
        # 1. Verify user belongs to this workspace
        if current_user.team_id != request.team_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # 2. Get workspace
        stmt = select(Workspace).where(Workspace.team_id == request.team_id)
        result = await db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # 3. Get or create IntegrationStatus for Slack
        from app.models.integration_status import IntegrationStatus

        stmt = select(IntegrationStatus).where(
            and_(
                IntegrationStatus.team_id == request.team_id,
                IntegrationStatus.source_type == "slack"
            )
        )
        result = await db.execute(stmt)
        integration_status = result.scalar_one_or_none()

        if not integration_status:
            integration_status = IntegrationStatus(
                team_id=request.team_id,
                source_type="slack",
                is_connected="true",
                connected_at=datetime.utcnow(),
                indexing_status="pending",
                config={}
            )
            db.add(integration_status)

        # 4. Save selected channels to config
        if not integration_status.config:
            integration_status.config = {}

        integration_status.config["selected_channels"] = request.channel_ids
        integration_status.config["channels_selected_at"] = datetime.utcnow().isoformat()

        await db.commit()
        await db.refresh(integration_status)

        # 5. Trigger indexing with selected channels
        from app.services.integration_orchestrator import get_integration_orchestrator

        orchestrator = get_integration_orchestrator(db)

        indexing_result = await orchestrator.handle_slack_installation(
            team_id=workspace.team_id,
            bot_token=workspace.bot_access_token,
            workspace=workspace
        )

        logger.info(
            "slack_indexing_triggered_after_channel_selection",
            team_id=workspace.team_id,
            channels_selected=len(request.channel_ids),
            job_id=indexing_result.get("job_id")
        )

        # 6. Return next step
        return {
            "success": True,
            "message": f"Indexing started for {len(request.channel_ids)} channels",
            "channels_selected": len(request.channel_ids),
            "job_id": indexing_result.get("job_id"),
            "estimated_minutes": indexing_result.get("estimated_minutes"),
            "next_step": {
                "action": "integration_choice",
                "url": f"/api/v1/onboarding/integration-choice?team_id={workspace.team_id}",
                "description": "Choose additional integrations"
            }
        }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(
            "select_channels_error",
            error=str(e),
            team_id=request.team_id
        )
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to save channel selection"
        )


class RepositorySelectionRequest(BaseModel):
    """Repository selection during onboarding"""
    team_id: str
    repository_names: List[str]  # e.g., ["org/repo1", "org/repo2"]


@router.get("/onboarding/repositories")
async def get_repositories_for_selection(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get accessible GitHub repositories for onboarding selection

    Returns repos with metadata for user to choose which to index
    """
    try:
        # 1. Verify user belongs to this workspace
        if current_user.team_id != team_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # 2. Get UserIntegrationConnection for GitHub
        from app.models.integration import UserIntegrationConnection

        stmt = select(UserIntegrationConnection).where(
            and_(
                UserIntegrationConnection.team_id == team_id,
                UserIntegrationConnection.user_id == current_user.user_id,
                UserIntegrationConnection.integration_type == "github",
                UserIntegrationConnection.is_active == True
            )
        )
        result = await db.execute(stmt)
        user_conn = result.scalar_one_or_none()

        if not user_conn:
            raise HTTPException(
                status_code=404,
                detail="GitHub not connected. Please complete GitHub OAuth first."
            )

        # 3. Decrypt GitHub token
        from app.core.encryption import get_encryption_service
        encryption_service = get_encryption_service()
        github_token = encryption_service.decrypt(user_conn.access_token_encrypted)

        # 4. Fetch repositories from GitHub API
        from app.services.github_services.github_api_client import GitHubAPIClient

        github_client = GitHubAPIClient(github_token)
        repos = await github_client.get_user_repos(
            visibility="all",
            affiliation="owner,collaborator,organization_member",
            sort="updated"
        )

        # 5. Format response
        formatted_repos = []
        for repo in repos:
            formatted_repos.append({
                "full_name": repo.get("full_name"),
                "description": repo.get("description") or "",
                "language": repo.get("language") or "Unknown",
                "visibility": repo.get("visibility", "private"),
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "updated_at": repo.get("updated_at"),
                "pre_selected": False  # None selected by default
            })

        logger.info(
            "repositories_fetched_for_selection",
            team_id=team_id,
            user_id=current_user.user_id,
            total_repos=len(formatted_repos)
        )

        return {
            "team_id": team_id,
            "github_user": user_conn.external_username,
            "repositories": formatted_repos,
            "total_repositories": len(formatted_repos),
            "recommendation": "Select repositories you want to search from Slack"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "get_repositories_error",
            error=str(e),
            team_id=team_id
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch repositories"
        )


@router.post("/onboarding/repositories/select")
async def select_repositories_and_trigger_indexing(
    request: RepositorySelectionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Save selected repositories and trigger GitHub indexing

    This endpoint:
    1. Saves selected repo names to Integration.config
    2. Creates IntegrationSyncJob for each repository
    3. Triggers background indexing via GitHub service
    4. Returns next onboarding step (dashboard)
    """
    try:
        # 1. Verify user belongs to this workspace
        if current_user.team_id != request.team_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # 2. Get Integration record (workspace-level)
        from app.models.integration import Integration

        stmt = select(Integration).where(
            and_(
                Integration.team_id == request.team_id,
                Integration.integration_type == "github"
            )
        )
        result = await db.execute(stmt)
        integration = result.scalar_one_or_none()

        if not integration:
            raise HTTPException(
                status_code=404,
                detail="GitHub integration not found"
            )

        # 3. Save selected repos to Integration.config
        if not integration.config:
            integration.config = {}

        integration.config["selected_repos"] = request.repository_names
        integration.config["repos_selected_at"] = datetime.utcnow().isoformat()

        await db.commit()

        # 4. Get UserIntegrationConnection for indexing
        from app.models.integration import UserIntegrationConnection

        stmt = select(UserIntegrationConnection).where(
            and_(
                UserIntegrationConnection.team_id == request.team_id,
                UserIntegrationConnection.user_id == current_user.user_id,
                UserIntegrationConnection.integration_type == "github",
                UserIntegrationConnection.is_active == True
            )
        )
        result = await db.execute(stmt)
        user_conn = result.scalar_one_or_none()

        # 5. Trigger indexing for each repository
        from app.services.github_services.github_indexing_service import github_indexing_service

        job_ids = []
        for repo_full_name in request.repository_names:
            owner, repo = repo_full_name.split('/')

            sync_job = await github_indexing_service.index_repository(
                connection=user_conn,
                repo_owner=owner,
                repo_name=repo,
                db=db
            )

            job_ids.append(sync_job.id if sync_job else None)

        logger.info(
            "github_indexing_triggered_after_repo_selection",
            team_id=request.team_id,
            repos_selected=len(request.repository_names),
            job_ids=job_ids
        )

        # 6. Return next step
        return {
            "success": True,
            "message": f"Indexing started for {len(request.repository_names)} repositories",
            "repositories_selected": len(request.repository_names),
            "job_ids": job_ids,
            "next_step": {
                "action": "dashboard",
                "url": f"/dashboard?team_id={request.team_id}",
                "description": "View indexing progress"
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "select_repositories_error",
            error=str(e),
            team_id=request.team_id
        )
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to save repository selection"
        )
