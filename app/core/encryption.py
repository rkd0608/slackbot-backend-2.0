"""Token encryption service using AES-256-GCM for OAuth tokens"""
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend
import os
import base64
from typing import Tuple
from app.core.logging import get_logger

logger = get_logger(__name__)


class EncryptionService:
    """
    Handles encryption and decryption of sensitive OAuth tokens
    Uses AES-256-GCM for authenticated encryption
    """

    def __init__(self, encryption_key: str):
        """
        Initialize encryption service with a base64-encoded 32-byte key

        Args:
            encryption_key: Base64-encoded 32-byte encryption key
        """
        try:
            # Decode the base64 key
            key_bytes = base64.b64decode(encryption_key)

            # Validate key length (must be 32 bytes for AES-256)
            if len(key_bytes) != 32:
                raise ValueError(f"Encryption key must be 32 bytes, got {len(key_bytes)}")

            self.aesgcm = AESGCM(key_bytes)
            logger.info("encryption_service_initialized", key_length=len(key_bytes))

        except Exception as e:
            logger.error("encryption_service_init_failed", error=str(e))
            raise

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string using AES-256-GCM

        Args:
            plaintext: The string to encrypt (e.g., OAuth access token)

        Returns:
            Base64-encoded string containing nonce + ciphertext + tag
            Format: base64(nonce || ciphertext)
        """
        try:
            # Generate a random 96-bit nonce (12 bytes is standard for GCM)
            nonce = os.urandom(12)

            # Encrypt the plaintext
            plaintext_bytes = plaintext.encode('utf-8')
            ciphertext = self.aesgcm.encrypt(nonce, plaintext_bytes, None)

            # Combine nonce + ciphertext and encode as base64
            encrypted_data = nonce + ciphertext
            encrypted_b64 = base64.b64encode(encrypted_data).decode('utf-8')

            logger.debug("token_encrypted", length=len(encrypted_b64))
            return encrypted_b64

        except Exception as e:
            logger.error("encryption_failed", error=str(e))
            raise

    def decrypt(self, encrypted_b64: str) -> str:
        """
        Decrypt an encrypted token

        Args:
            encrypted_b64: Base64-encoded string from encrypt()

        Returns:
            Decrypted plaintext string
        """
        try:
            # Decode from base64
            encrypted_data = base64.b64decode(encrypted_b64)

            # Split nonce and ciphertext
            nonce = encrypted_data[:12]
            ciphertext = encrypted_data[12:]

            # Decrypt
            plaintext_bytes = self.aesgcm.decrypt(nonce, ciphertext, None)
            plaintext = plaintext_bytes.decode('utf-8')

            logger.debug("token_decrypted")
            return plaintext

        except Exception as e:
            logger.error("decryption_failed", error=str(e))
            raise ValueError("Failed to decrypt token - data may be corrupted or key is incorrect")


def generate_encryption_key() -> str:
    """
    Generate a new random 32-byte encryption key for AES-256

    Returns:
        Base64-encoded 32-byte key suitable for use in environment variables
    """
    key_bytes = os.urandom(32)
    key_b64 = base64.b64encode(key_bytes).decode('utf-8')
    return key_b64


# Global encryption service instance (initialized from settings)
_encryption_service = None


def get_encryption_service() -> EncryptionService:
    """Get or create the global encryption service instance"""
    global _encryption_service

    if _encryption_service is None:
        from app.core.config import settings
        _encryption_service = EncryptionService(settings.oauth_token_encryption_key)

    return _encryption_service
