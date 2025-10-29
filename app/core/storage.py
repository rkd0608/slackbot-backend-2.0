"""AWS S3 storage management"""
from typing import Optional, BinaryIO
import boto3
from botocore.exceptions import ClientError
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class StorageManager:
    """Manages AWS S3 file storage operations"""

    def __init__(self):
        self.s3_client = None
        self.bucket = settings.aws_s3_bucket

    def initialize(self):
        """Initialize S3 client"""
        try:
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                region_name=settings.aws_region
            )

            # Verify bucket exists
            self.s3_client.head_bucket(Bucket=self.bucket)
            logger.info("storage_initialized", bucket=self.bucket)
        except (ClientError, Exception) as e:
            logger.warning(
                "storage_init_failed",
                bucket=self.bucket,
                error=str(e),
                message="S3 storage unavailable - file operations will be disabled"
            )
            self.s3_client = None

    def upload_file(self, file_data: bytes, key: str, content_type: Optional[str] = None) -> bool:
        """Upload file to S3"""
        if not self.s3_client:
            logger.warning("upload_skipped", key=key, reason="S3 client not initialized")
            return False

        try:
            extra_args = {
                'ServerSideEncryption': 'AES256'  # Enable server-side encryption
            }
            if content_type:
                extra_args['ContentType'] = content_type

            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=file_data,
                **extra_args
            )
            logger.info("file_uploaded", key=key, encrypted=True)
            return True
        except ClientError as e:
            logger.error("file_upload_error", key=key, error=str(e))
            return False

    def download_file(self, key: str) -> Optional[bytes]:
        """Download file from S3"""
        if not self.s3_client:
            logger.warning("download_skipped", key=key, reason="S3 client not initialized")
            return None

        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            return response['Body'].read()
        except ClientError as e:
            logger.error("file_download_error", key=key, error=str(e))
            return None

    def delete_file(self, key: str) -> bool:
        """Delete file from S3"""
        if not self.s3_client:
            logger.warning("delete_skipped", key=key, reason="S3 client not initialized")
            return False

        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=key)
            logger.info("file_deleted", key=key)
            return True
        except ClientError as e:
            logger.error("file_delete_error", key=key, error=str(e))
            return False

    def generate_presigned_url(self, key: str, expiration: int = 3600) -> Optional[str]:
        """Generate presigned URL for file access"""
        if not self.s3_client:
            logger.warning("presigned_url_skipped", key=key, reason="S3 client not initialized")
            return None

        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': key},
                ExpiresIn=expiration
            )
            return url
        except ClientError as e:
            logger.error("presigned_url_error", key=key, error=str(e))
            return None


# Global storage manager instance
storage_manager = StorageManager()
