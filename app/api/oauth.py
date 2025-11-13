"""Slack OAuth endpoints for workspace installation"""
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from app.services.oauth_service import oauth_service
from app.core.database import get_db
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/oauth/install")
async def initiate_oauth():
    """Initiate Slack OAuth flow - redirects user to Slack authorization page"""

    try:
        oauth_data = await oauth_service.generate_oauth_url()

        logger.info("oauth_initiated")

        # Redirect to Slack OAuth page
        return RedirectResponse(url=oauth_data["oauth_url"])

    except Exception as e:
        logger.error("oauth_initiate_error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to initiate OAuth")


@router.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(..., description="Authorization code from Slack"),
    state: str = Query(default="", description="State token for CSRF protection (optional when using direct Slack link)"),
    error: str = Query(None, description="Error from Slack"),
    request: Request = None,
    db: AsyncSession = Depends(get_db)
):
    """OAuth callback endpoint - handles authorization code exchange"""

    # Check for OAuth errors
    if error:
        logger.error("oauth_callback_error", error=error)
        return RedirectResponse(url=f"/install/error?error={error}")

    try:
        # Verify OAuth state token (CSRF protection) - only if state is provided
        if state:
            is_valid_state = await oauth_service.verify_state_token(state)
            if not is_valid_state:
                logger.warning("oauth_state_verification_failed")
                raise HTTPException(status_code=400, detail="Invalid state token")
        else:
            logger.info("oauth_without_state", message="Direct Slack OAuth link used (no state token)")

        # Exchange code for access tokens
        oauth_data = await oauth_service.exchange_code_for_token(code, db)

        # Get workspace information - use BOT token, not user token
        # oauth_data["access_token"] is the BOT token
        bot_token = oauth_data["access_token"]
        workspace_info = await oauth_service.get_workspace_info(bot_token)

        # Get installer email from OAuth
        installer_email = oauth_data.get("authed_user", {}).get("email")

        # Create/update workspace record
        workspace = await oauth_service.create_workspace(
            oauth_data=oauth_data,
            workspace_info=workspace_info,
            installer_email=installer_email,
            db=db
        )

        # Create or update installer user record
        from app.models.user import User
        from sqlalchemy import select

        # Try to extract user ID with multiple fallbacks
        authed_user = oauth_data.get("authed_user", {})
        slack_user_id = authed_user.get("id") or authed_user.get("user_id") or workspace.installer_user_id

        # Check if user already exists
        stmt = select(User).where(User.user_id == slack_user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if user:
            # Update existing user
            user.email = installer_email
            user.role = "admin"
            user.is_admin = 1
            user.updated_at = datetime.utcnow()
        else:
            # Create new user with minimal info (profile incomplete)
            user = User(
                user_id=slack_user_id,
                team_id=workspace.team_id,
                email=installer_email,
                role="admin",
                is_admin=1,
                profile_completed=0,  # Profile needs to be completed
                created_at=datetime.utcnow()
            )
            db.add(user)

        # Store user's access token (Glean-style user-centric access)
        from app.core.encryption import get_encryption_service

        encryption_service = get_encryption_service()
        authed_user = oauth_data.get("authed_user", {})

        if authed_user.get("access_token"):
            try:
                # Encrypt and store user access token
                user.access_token = encryption_service.encrypt(authed_user.get("access_token"))
                user.token_expires_at = None  # Slack user tokens don't expire automatically

                logger.info(
                    "user_token_stored",
                    user_id=user.user_id,
                    team_id=workspace.team_id,
                    scopes=authed_user.get("scope", "").split(",") if authed_user.get("scope") else []
                )
            except Exception as e:
                logger.error(
                    "user_token_storage_failed",
                    error=str(e),
                    user_id=user.user_id
                )
                # Don't fail the entire OAuth flow if token storage fails

        await db.commit()
        await db.refresh(user)
        await db.refresh(workspace)

        # Log successful installation
        logger.info(
            "oauth_completed",
            team_id=workspace.team_id,
            team_name=workspace.team_name,
            is_trial=workspace.is_trial,
            profile_completed=user.profile_completed
        )

        # Note: Indexing is NOT triggered automatically here
        # Users will select which channels to index during onboarding flow
        # Indexing will be triggered from the channel selection endpoint

        # Redirect to frontend
        from app.core.config import settings

        if not user.profile_completed:
            # New user - redirect to profile completion page
            frontend_url = f"{settings.frontend_url}/onboarding/profile?team_id={workspace.team_id}&user_id={user.user_id}"
            return RedirectResponse(url=frontend_url)
        else:
            # Existing user (profile already completed) - redirect to login
            # This handles reinstallation or re-authorization scenarios
            logger.info(
                "existing_user_reinstall",
                user_id=user.user_id,
                team_id=workspace.team_id,
                message="Existing user clicked Add to Slack again"
            )
            frontend_url = f"{settings.frontend_url}/login?message=reinstall&team_id={workspace.team_id}"
            return RedirectResponse(url=frontend_url)

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("oauth_callback_error", error=str(e))
        raise HTTPException(status_code=500, detail="OAuth callback failed")


@router.get("/install/success")
async def installation_success(
    team_id: str = Query(...),
    trial: bool = Query(False),
    db: AsyncSession = Depends(get_db)
):
    """Installation success page"""

    # This endpoint would return HTML or redirect to frontend
    # For now, returning JSON for API response

    return {
        "success": True,
        "message": "Workspace successfully installed!",
        "team_id": team_id,
        "is_trial": trial,
        "trial_days": 14 if trial else 0,
        "next_steps": [
            "We're indexing your Slack workspace messages",
            "You'll receive a DM when indexing is complete",
            "Start asking questions using /ask command",
            "Manage settings in the dashboard"
        ]
    }


@router.get("/install/error")
async def installation_error(error: str = Query(...)):
    """Installation error page"""

    error_messages = {
        "access_denied": "Installation was cancelled. Please try again if you want to use the app.",
        "invalid_code": "Invalid authorization code. Please try installing again.",
        "invalid_state": "Security validation failed. Please try again."
    }

    message = error_messages.get(error, "An error occurred during installation.")

    return {
        "success": False,
        "error": error,
        "message": message,
        "retry_url": "/oauth/install"
    }
