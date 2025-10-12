"""Slack OAuth endpoints for workspace installation"""
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
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
    state: str = Query(..., description="State token for CSRF protection"),
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
        # Verify state token (CSRF protection)
        is_valid_state = await oauth_service.verify_state_token(state)
        if not is_valid_state:
            logger.warning("oauth_state_verification_failed")
            raise HTTPException(status_code=400, detail="Invalid state token")

        # Exchange code for access tokens
        oauth_data = await oauth_service.exchange_code_for_token(code, db)

        # Get workspace information
        access_token = oauth_data["access_token"]
        workspace_info = await oauth_service.get_workspace_info(access_token)

        # Get installer email (optional)
        installer_email = oauth_data.get("authed_user", {}).get("email")

        # Create/update workspace record
        workspace = await oauth_service.create_workspace(
            oauth_data=oauth_data,
            workspace_info=workspace_info,
            installer_email=installer_email,
            db=db
        )

        # Log successful installation
        logger.info(
            "oauth_completed",
            team_id=workspace.team_id,
            team_name=workspace.team_name,
            is_trial=workspace.is_trial
        )

        # Redirect to success page or dashboard
        if workspace.subscription_status == "trial":
            # New trial - redirect to onboarding
            return RedirectResponse(url=f"/install/success?team_id={workspace.team_id}&trial=true")
        else:
            # Existing workspace - redirect to dashboard
            return RedirectResponse(url=f"/dashboard?team_id={workspace.team_id}")

    except HTTPException:
        raise
    except Exception as e:
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
