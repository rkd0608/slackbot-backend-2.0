"""Authentication dependencies and middleware"""
from fastapi import Depends, HTTPException, status, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.models.user import User, APIKey
from app.services.auth_service import auth_service
from app.core.logging import get_logger
from datetime import datetime

logger = get_logger(__name__)

# HTTP Bearer token security scheme
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current authenticated user from JWT token"""

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Verify and decode token
    payload = auth_service.verify_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user ID from token
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )

    # Fetch user from database
    stmt = select(User).where(User.user_id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    if user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deleted"
        )

    # Update last login
    user.last_login_at = datetime.utcnow()
    await db.commit()

    return user


async def get_current_admin_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Get current user and verify they are a workspace admin"""

    if not current_user.is_workspace_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )

    return current_user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """Get current user if authenticated, otherwise None"""

    if not credentials:
        return None

    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


async def verify_api_key(
    x_api_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> Optional[str]:
    """Verify API key and return team_id"""

    if not x_api_key:
        return None

    # Hash the provided API key
    from app.services.auth_service import pwd_context

    # Find API key in database
    stmt = select(APIKey).where(APIKey.is_active == 1)
    result = await db.execute(stmt)
    api_keys = result.scalars().all()

    for api_key in api_keys:
        # Verify key hash
        if pwd_context.verify(x_api_key, api_key.key_hash):
            # Check if expired
            if api_key.is_expired:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has expired"
                )

            # Update usage tracking
            api_key.last_used_at = datetime.utcnow()
            api_key.usage_count += 1
            await db.commit()

            logger.info(
                "api_key_used",
                key_name=api_key.name,
                team_id=api_key.team_id,
                usage_count=api_key.usage_count
            )

            return api_key.team_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key"
    )


async def get_current_user_or_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Allow authentication via JWT token OR API key"""

    # Try JWT first
    if credentials:
        try:
            user = await get_current_user(credentials, db)
            return {
                "auth_type": "jwt",
                "user": user,
                "team_id": user.team_id
            }
        except HTTPException:
            pass

    # Try API key
    if x_api_key:
        team_id = await verify_api_key(x_api_key, db)
        return {
            "auth_type": "api_key",
            "user": None,
            "team_id": team_id
        }

    # No valid authentication
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required (JWT token or API key)",
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_workspace_access(team_id: str):
    """Dependency to verify user has access to specific workspace"""

    async def _verify(
        current_user: User = Depends(get_current_user)
    ):
        if current_user.team_id != team_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this workspace"
            )
        return current_user

    return _verify


class RoleChecker:
    """Dependency class to check user role"""

    def __init__(self, allowed_roles: list):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: User = Depends(get_current_user)):
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role: {', '.join(self.allowed_roles)}"
            )
        return current_user


# Convenient role checkers
require_admin = RoleChecker(allowed_roles=["admin"])
require_member = RoleChecker(allowed_roles=["admin", "member"])
require_viewer = RoleChecker(allowed_roles=["admin", "member", "viewer"])
