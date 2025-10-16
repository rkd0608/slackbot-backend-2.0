"""Onboarding API endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from typing import Optional
from app.core.database import get_db
from app.core.logging import get_logger
from app.services.auth_service import auth_service
from app.core.cache import cache_manager
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
                "url": f"/onboarding/channels?team_id={workspace.team_id}"
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
