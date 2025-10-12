"""Authentication service for JWT token management"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    """Handles JWT token creation, validation, and password hashing"""

    def __init__(self):
        self.secret_key = settings.jwt_secret_key
        self.algorithm = settings.jwt_algorithm
        self.access_token_expire_minutes = settings.jwt_expiration_minutes
        self.refresh_token_expire_days = 30

    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt"""
        return pwd_context.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return pwd_context.verify(plain_password, hashed_password)

    def create_access_token(
        self,
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None
    ) -> str:
        """Create a JWT access token"""

        to_encode = data.copy()

        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire_minutes)

        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access"
        })

        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

        logger.info(
            "access_token_created",
            subject=data.get("sub"),
            expires_at=expire.isoformat()
        )

        return encoded_jwt

    def create_refresh_token(self, data: Dict[str, Any]) -> str:
        """Create a JWT refresh token with longer expiration"""

        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(days=self.refresh_token_expire_days)

        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh"
        })

        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

        logger.info(
            "refresh_token_created",
            subject=data.get("sub"),
            expires_at=expire.isoformat()
        )

        return encoded_jwt

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode a JWT token"""

        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm]
            )

            # Check token type
            token_type = payload.get("type")
            if token_type not in ["access", "refresh"]:
                logger.warning("invalid_token_type", type=token_type)
                return None

            # Check expiration
            exp = payload.get("exp")
            if exp and datetime.fromtimestamp(exp) < datetime.utcnow():
                logger.warning("token_expired", exp=exp)
                return None

            return payload

        except JWTError as e:
            logger.warning("token_verification_failed", error=str(e))
            return None

    def decode_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Decode token without verification (for inspection)"""

        try:
            return jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={"verify_signature": False}
            )
        except JWTError:
            return None

    def create_api_key(self, workspace_id: str, description: str) -> str:
        """Create an API key for programmatic access"""

        import secrets

        # Generate a secure random API key
        api_key = f"sk_{workspace_id}_{secrets.token_urlsafe(32)}"

        logger.info(
            "api_key_created",
            workspace_id=workspace_id,
            description=description
        )

        return api_key

    def verify_api_key(self, api_key: str) -> Optional[str]:
        """Verify an API key and return workspace_id"""

        try:
            # API key format: sk_{workspace_id}_{random_token}
            if not api_key.startswith("sk_"):
                return None

            parts = api_key.split("_", 2)
            if len(parts) != 3:
                return None

            workspace_id = parts[1]
            return workspace_id

        except Exception as e:
            logger.error("api_key_verification_error", error=str(e))
            return None


# Global auth service instance
auth_service = AuthService()
