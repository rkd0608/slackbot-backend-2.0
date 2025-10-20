"""AWS Secrets Manager integration for secure credential management"""
import json
import os
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError
from app.core.logging import get_logger

logger = get_logger(__name__)


class SecretsManager:
    """Manages secrets retrieval from AWS Secrets Manager"""

    def __init__(self):
        self.client = None
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.secret_name = os.getenv("AWS_SECRET_NAME", "slackbot/production")
        self.use_secrets_manager = os.getenv("USE_SECRETS_MANAGER", "false").lower() == "true"
        self._cached_secrets: Optional[Dict[str, Any]] = None

    def initialize(self):
        """Initialize AWS Secrets Manager client"""
        if not self.use_secrets_manager:
            logger.info("secrets_manager_disabled", message="Using environment variables")
            return

        try:
            self.client = boto3.client(
                'secretsmanager',
                region_name=self.region
            )
            logger.info("secrets_manager_initialized", region=self.region, secret_name=self.secret_name)
        except Exception as e:
            logger.error("secrets_manager_init_failed", error=str(e))
            raise

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get a secret value by key.
        Falls back to environment variable if Secrets Manager is not enabled.
        """
        # If Secrets Manager is disabled, use environment variables
        if not self.use_secrets_manager:
            return os.getenv(key, default)

        # Use cached secrets if available
        if self._cached_secrets is None:
            self._load_secrets()

        if self._cached_secrets is None:
            # Fallback to environment variable if secrets loading failed
            logger.warning("secrets_fallback_to_env", key=key)
            return os.getenv(key, default)

        return self._cached_secrets.get(key, default)

    def _load_secrets(self):
        """Load all secrets from AWS Secrets Manager"""
        if not self.client:
            logger.warning("secrets_client_not_initialized")
            return

        try:
            response = self.client.get_secret_value(SecretId=self.secret_name)

            # Secrets can be stored as SecretString (JSON) or SecretBinary
            if 'SecretString' in response:
                secret_string = response['SecretString']
                self._cached_secrets = json.loads(secret_string)
                logger.info("secrets_loaded", count=len(self._cached_secrets))
            else:
                logger.error("secrets_binary_not_supported")
                self._cached_secrets = {}

        except ClientError as e:
            error_code = e.response['Error']['Code']

            if error_code == 'ResourceNotFoundException':
                logger.error("secret_not_found", secret_name=self.secret_name)
            elif error_code == 'InvalidRequestException':
                logger.error("invalid_secret_request", error=str(e))
            elif error_code == 'InvalidParameterException':
                logger.error("invalid_secret_parameter", error=str(e))
            elif error_code == 'DecryptionFailure':
                logger.error("secret_decryption_failed", error=str(e))
            elif error_code == 'InternalServiceError':
                logger.error("secrets_service_error", error=str(e))
            else:
                logger.error("secrets_unknown_error", error=str(e), code=error_code)

            self._cached_secrets = None

        except Exception as e:
            logger.error("secrets_load_error", error=str(e))
            self._cached_secrets = None

    def refresh_secrets(self):
        """Force refresh of cached secrets"""
        logger.info("refreshing_secrets")
        self._cached_secrets = None
        self._load_secrets()

    def get_all_secrets(self) -> Dict[str, Any]:
        """Get all secrets (for debugging, use with caution)"""
        if self._cached_secrets is None:
            self._load_secrets()

        return self._cached_secrets or {}


# Global secrets manager instance
secrets_manager = SecretsManager()
