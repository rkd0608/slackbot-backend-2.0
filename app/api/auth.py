"""Authentication endpoints"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from typing import Optional
from app.core.database import get_db
from app.models.user import User, APIKey
from app.services.auth_service import auth_service
from app.core.auth import get_current_user, get_current_admin_user
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()
security = HTTPBearer()


class LoginRequest(BaseModel):
    """Login request with email and password"""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """JWT token response"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    """Refresh token request"""
    refresh_token: str


class RegisterRequest(BaseModel):
    """User registration request"""
    email: EmailStr
    password: str
    slack_user_id: str
    team_id: str
    display_name: Optional[str] = None


class APIKeyRequest(BaseModel):
    """Create API key request"""
    name: str
    description: Optional[str] = None
    expires_in_days: Optional[int] = None


class APIKeyResponse(BaseModel):
    """API key response (only shown once)"""
    api_key: str
    key_prefix: str
    name: str
    created_at: str
    expires_at: Optional[str] = None


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Login with email and password

    Returns JWT access and refresh tokens
    """

    try:
        # Find user by email
        stmt = select(User).where(User.email == request.email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user or not user.password_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )

        # Verify password
        if not auth_service.verify_password(request.password, user.password_hash):
            logger.warning("login_failed_invalid_password", email=request.email)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )

        # Check if user is deleted
        if user.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is deactivated"
            )

        # Create tokens
        token_data = {
            "sub": user.user_id,
            "email": user.email,
            "team_id": user.team_id,
            "role": user.role
        }

        access_token = auth_service.create_access_token(token_data)
        refresh_token = auth_service.create_refresh_token(token_data)

        # Update last login
        user.last_login_at = datetime.utcnow()
        await db.commit()

        logger.info(
            "user_logged_in",
            user_id=user.user_id,
            email=user.email,
            team_id=user.team_id
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=auth_service.access_token_expire_minutes * 60
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("login_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed"
        )


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Refresh access token using refresh token

    Returns new access and refresh tokens
    """

    try:
        # Verify refresh token
        payload = auth_service.verify_token(request.refresh_token)

        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token"
            )

        # Check token type
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )

        # Get user
        user_id = payload.get("sub")
        stmt = select(User).where(User.user_id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user or user.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )

        # Create new tokens
        token_data = {
            "sub": user.user_id,
            "email": user.email,
            "team_id": user.team_id,
            "role": user.role
        }

        access_token = auth_service.create_access_token(token_data)
        new_refresh_token = auth_service.create_refresh_token(token_data)

        logger.info("token_refreshed", user_id=user.user_id)

        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=auth_service.access_token_expire_minutes * 60
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("refresh_token_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed"
        )


@router.post("/auth/register", response_model=TokenResponse)
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Register new user (typically workspace installer)

    Returns JWT tokens
    """

    try:
        # Check if user already exists
        stmt = select(User).where(User.email == request.email)
        result = await db.execute(stmt)
        existing_user = result.scalar_one_or_none()

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

        # Check Slack user ID
        stmt = select(User).where(User.user_id == request.slack_user_id)
        result = await db.execute(stmt)
        existing_slack_user = result.scalar_one_or_none()

        if existing_slack_user:
            # User exists from Slack data, just add password
            existing_slack_user.email = request.email
            existing_slack_user.password_hash = auth_service.hash_password(request.password)
            existing_slack_user.role = "admin"  # First user is admin
            existing_slack_user.is_admin = 1
            await db.commit()

            user = existing_slack_user
        else:
            # Create new user
            user = User(
                user_id=request.slack_user_id,
                team_id=request.team_id,
                email=request.email,
                display_name=request.display_name,
                password_hash=auth_service.hash_password(request.password),
                role="admin",  # First user is admin
                is_admin=1,
                created_at=datetime.utcnow()
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        # Create tokens
        token_data = {
            "sub": user.user_id,
            "email": user.email,
            "team_id": user.team_id,
            "role": user.role
        }

        access_token = auth_service.create_access_token(token_data)
        refresh_token = auth_service.create_refresh_token(token_data)

        logger.info(
            "user_registered",
            user_id=user.user_id,
            email=user.email,
            team_id=user.team_id
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=auth_service.access_token_expire_minutes * 60
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("register_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed"
        )


@router.post("/auth/logout")
async def logout(
    current_user: User = Depends(get_current_user)
):
    """
    Logout user (client should discard tokens)

    In production, you would blacklist the token in Redis
    """

    logger.info("user_logged_out", user_id=current_user.user_id)

    return {
        "message": "Logged out successfully",
        "user_id": current_user.user_id
    }


@router.get("/auth/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """Get current authenticated user info"""

    return {
        "user_id": current_user.user_id,
        "email": current_user.email,
        "display_name": current_user.display_name,
        "team_id": current_user.team_id,
        "role": current_user.role,
        "is_admin": bool(current_user.is_admin),
        "last_login_at": current_user.last_login_at.isoformat() if current_user.last_login_at else None
    }


@router.post("/auth/api-keys", response_model=APIKeyResponse)
async def create_api_key(
    request: APIKeyRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create API key for programmatic access (admin only)

    ⚠️ API key is only shown once! Save it securely.
    """

    try:
        # Generate API key
        api_key = auth_service.create_api_key(
            workspace_id=current_user.team_id,
            description=request.description or ""
        )

        # Hash the key for storage
        key_hash = auth_service.hash_password(api_key)
        key_prefix = api_key[:15]  # sk_T123ABC_...

        # Calculate expiration
        expires_at = None
        if request.expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=request.expires_in_days)

        # Create API key record
        api_key_record = APIKey(
            key_hash=key_hash,
            key_prefix=key_prefix,
            name=request.name,
            description=request.description,
            team_id=current_user.team_id,
            created_by_user_id=current_user.id,
            expires_at=expires_at,
            created_at=datetime.utcnow()
        )

        db.add(api_key_record)
        await db.commit()

        logger.info(
            "api_key_created",
            key_name=request.name,
            team_id=current_user.team_id,
            created_by=current_user.user_id
        )

        return APIKeyResponse(
            api_key=api_key,  # Only shown once!
            key_prefix=key_prefix,
            name=request.name,
            created_at=api_key_record.created_at.isoformat(),
            expires_at=expires_at.isoformat() if expires_at else None
        )

    except Exception as e:
        logger.error("create_api_key_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create API key"
        )


@router.get("/auth/api-keys")
async def list_api_keys(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """List all API keys for workspace (admin only)"""

    try:
        stmt = select(APIKey).where(
            APIKey.team_id == current_user.team_id
        ).order_by(APIKey.created_at.desc())

        result = await db.execute(stmt)
        api_keys = result.scalars().all()

        return {
            "api_keys": [
                {
                    "id": key.id,
                    "name": key.name,
                    "key_prefix": key.key_prefix,
                    "description": key.description,
                    "is_active": bool(key.is_active),
                    "is_expired": key.is_expired,
                    "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
                    "usage_count": key.usage_count,
                    "expires_at": key.expires_at.isoformat() if key.expires_at else None,
                    "created_at": key.created_at.isoformat()
                }
                for key in api_keys
            ]
        }

    except Exception as e:
        logger.error("list_api_keys_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list API keys"
        )


@router.delete("/auth/api-keys/{key_id}")
async def revoke_api_key(
    key_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Revoke API key (admin only)"""

    try:
        stmt = select(APIKey).where(
            APIKey.id == key_id,
            APIKey.team_id == current_user.team_id
        )
        result = await db.execute(stmt)
        api_key = result.scalar_one_or_none()

        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="API key not found"
            )

        # Deactivate key
        api_key.is_active = 0
        api_key.updated_at = datetime.utcnow()
        await db.commit()

        logger.info(
            "api_key_revoked",
            key_id=key_id,
            key_name=api_key.name,
            team_id=current_user.team_id
        )

        return {
            "message": "API key revoked",
            "key_id": key_id,
            "key_name": api_key.name
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("revoke_api_key_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke API key"
        )
