"""GitHub OAuth API endpoints"""
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
import secrets
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.auth import get_current_user
from app.core.config import settings
from app.services.github_services.github_oauth_service import get_github_oauth_service
from app.models.user import User

router = APIRouter(prefix="/oauth/github", tags=["github-oauth"])
logger = get_logger(__name__)

# In-memory state storage (in production, use Redis)
_oauth_states = {}


@router.get("/authorize")
async def github_authorize(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Initiate GitHub OAuth flow

    Returns authorization URL for frontend to redirect user
    """
    try:
        github_service = get_github_oauth_service()

        # Generate CSRF state token
        state = secrets.token_urlsafe(32)

        # Store state with user info (expires in 10 minutes)
        _oauth_states[state] = {
            "user_id": current_user.user_id,
            "team_id": current_user.team_id,
            "created_at": secrets.token_hex(16)  # Timestamp placeholder
        }

        # Generate authorization URL
        auth_url = github_service.generate_authorization_url(state)

        logger.info(
            "github_oauth_initiated",
            user_id=current_user.user_id,
            team_id=current_user.team_id
        )

        # Return authorization URL as JSON for frontend to handle redirect
        return {
            "authorization_url": auth_url,
            "state": state
        }

    except Exception as e:
        logger.error("github_oauth_initiation_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to initiate GitHub OAuth")


@router.get("/callback")
async def github_callback(
    code: str = Query(..., description="Authorization code from GitHub"),
    state: str = Query(..., description="CSRF state token"),
    db: AsyncSession = Depends(get_db)
):
    """
    GitHub OAuth callback endpoint

    Handles redirect from GitHub after user authorization
    """
    try:
        # Validate state
        if state not in _oauth_states:
            logger.error("github_oauth_invalid_state", state=state)
            raise HTTPException(status_code=400, detail="Invalid or expired state token")

        # Get user info from state
        state_data = _oauth_states.pop(state)
        user_id = state_data["user_id"]
        team_id = state_data["team_id"]

        github_service = get_github_oauth_service()

        # Exchange code for token
        token_data = await github_service.exchange_code_for_token(code)
        access_token = token_data["access_token"]
        scopes = token_data.get("scope", "").split(",") if token_data.get("scope") else []

        # Get GitHub user info
        github_user_info = await github_service.get_user_info(access_token)

        # Get or create integration
        integration = await github_service.get_or_create_integration(
            db=db,
            team_id=team_id,
            enabled_by_user_id=user_id
        )

        # Store connection
        connection = await github_service.store_connection(
            db=db,
            user_id=user_id,
            team_id=team_id,
            integration_id=integration.id,
            access_token=access_token,
            github_user_info=github_user_info,
            scopes=scopes
        )

        logger.info(
            "github_oauth_completed",
            user_id=user_id,
            github_user_id=github_user_info["id"],
            github_username=github_user_info["login"]
        )

        # Redirect to frontend success page
        frontend_url = settings.frontend_url if hasattr(settings, 'frontend_url') else settings.app_base_url
        return RedirectResponse(
            url=f"{frontend_url}/integrations/github/success?username={github_user_info['login']}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("github_oauth_callback_failed", error=str(e))

        # Redirect to frontend error page
        frontend_url = settings.frontend_url if hasattr(settings, 'frontend_url') else settings.app_base_url
        return RedirectResponse(
            url=f"{frontend_url}/integrations/github/error?message={str(e)}"
        )


@router.get("/status")
async def github_connection_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Check GitHub connection status for current user

    Returns connection info if connected, null otherwise
    """
    try:
        github_service = get_github_oauth_service()

        connection = await github_service.get_user_connection(
            db=db,
            user_id=current_user.user_id,
            team_id=current_user.team_id
        )

        if not connection:
            return {
                "connected": False,
                "github_user": None
            }

        return {
            "connected": True,
            "github_user": {
                "id": connection.external_user_id,
                "username": connection.external_username,
                "email": connection.external_email,
                "scopes": connection.scopes,
                "connected_at": connection.connected_at.isoformat(),
                "last_synced_at": connection.last_synced_at.isoformat() if connection.last_synced_at else None
            }
        }

    except Exception as e:
        logger.error("github_status_check_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to check GitHub connection status")


@router.post("/disconnect")
async def github_disconnect(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Disconnect GitHub integration for current user and delete all data

    This will:
    - Delete all indexed repositories and files from the database
    - Delete all embeddings from Pinecone
    - Delete all sync job history
    - Delete the OAuth connection
    """
    try:
        github_service = get_github_oauth_service()

        result = await github_service.disconnect_user(
            db=db,
            user_id=current_user.user_id,
            team_id=current_user.team_id,
            delete_all_data=True
        )

        if not result['success']:
            raise HTTPException(status_code=404, detail=result['message'])

        logger.info(
            "github_disconnected",
            user_id=current_user.user_id,
            team_id=current_user.team_id,
            stats=result['stats']
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("github_disconnect_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to disconnect GitHub: {str(e)}")


@router.get("/scopes")
async def github_required_scopes():
    """
    Get required OAuth scopes for GitHub integration

    Returns list of scopes that will be requested
    """
    from app.services.github_services.github_oauth_service import GitHubOAuthService

    return {
        "scopes": GitHubOAuthService.DEFAULT_SCOPES,
        "scope_descriptions": {
            "repo": "Access to private repositories for code indexing",
            "read:org": "Read organization membership for permission filtering",
            "read:user": "Read user profile information",
            "user:email": "Access user email address"
        }
    }
