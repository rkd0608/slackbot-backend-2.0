"""File processing service for downloading and extracting content"""
import io
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.file import File
from app.core.storage import storage_manager
from app.services.slack_client import slack_client_manager
from app.core.logging import get_logger
from app.core.queue import queue_manager
from app.core.config import settings

logger = get_logger(__name__)


class FileProcessor:
    """Processes Slack files - download, extract content, store"""

    # Supported file types for text extraction
    TEXT_EXTRACTABLE = {
        'text', 'python', 'javascript', 'java', 'go', 'rust',
        'markdown', 'json', 'xml', 'yaml', 'csv', 'sql', 'html'
    }

    PDF_TYPES = {'pdf'}
    IMAGE_TYPES = {'png', 'jpg', 'jpeg', 'gif', 'bmp'}
    DOC_TYPES = {'doc', 'docx', 'odt', 'rtf'}

    async def process_file(
        self,
        file_info: Dict[str, Any],
        channel_id: str,
        user_id: str,
        team_id: str,
        db: AsyncSession,
        message_id: Optional[str] = None
    ) -> Optional[File]:
        """Process a file from Slack"""

        file_id = file_info.get("id")

        if not file_id:
            logger.error("file_processing_missing_id", file_info=str(file_info)[:200])
            return None

        # If file_info only has minimal data (just id), fetch complete info from Slack
        if not file_info.get("url_private"):
            logger.info("fetching_complete_file_info", file_id=file_id)
            complete_file_info = await slack_client_manager.get_file_info(file_id)
            if complete_file_info:
                file_info = complete_file_info
            else:
                logger.error("failed_to_fetch_file_info", file_id=file_id)
                return None

        # Check if already processed
        result = await db.execute(
            select(File).where(File.file_id == file_id)
        )
        existing = result.scalar_one_or_none()

        # If file is processed but missing embedding, queue it
        if existing and existing.is_processed and existing.vector_id is not None:
            logger.info("file_already_processed", file_id=file_id, has_embedding=True)
            return existing

        # If file is processed but missing embedding, queue it directly
        if existing and existing.is_processed and existing.vector_id is None:
            logger.info("file_processed_but_missing_embedding", file_id=file_id)

            # Get extracted text from S3 or database
            extracted_text = existing.extracted_text
            if not extracted_text and existing.s3_text_key:
                # Try to fetch from S3
                text_data = storage_manager.download_file(existing.s3_text_key)
                if text_data:
                    extracted_text = text_data.decode('utf-8')
                    logger.info("fetched_text_from_s3", file_id=file_id, text_length=len(extracted_text))

            if extracted_text:
                # Queue for embedding generation (send file_id only, not full text)
                logger.info("queueing_file_for_embedding", file_id=file_id, text_length=len(extracted_text))
                publish_success = await queue_manager.publish(
                    queue=queue_manager.EMBEDDINGS_QUEUE,
                    message={
                        "type": "file",
                        "file_id": file_id,
                        "channel_id": channel_id
                    }
                )
                if publish_success:
                    logger.info("file_queued_for_embedding", file_id=file_id)
                else:
                    logger.error("failed_to_queue_embedding", file_id=file_id)
            else:
                logger.warning("no_text_available_for_embedding", file_id=file_id)

            return existing

        # Create or update file record
        if existing:
            # Update existing record with new data
            existing.message_id = message_id or existing.message_id
            existing.channel_id = channel_id or existing.channel_id
            existing.user_id = user_id or existing.user_id
            existing.filename = file_info.get("name", existing.filename)
            existing.title = file_info.get("title", existing.title)
            existing.mimetype = file_info.get("mimetype", existing.mimetype)
            existing.filetype = file_info.get("filetype", existing.filetype)
            existing.size = file_info.get("size", existing.size)
            existing.slack_url = file_info.get("url_private", existing.slack_url)
            file_record = existing
        else:
            # Create new record
            file_record = File(
                file_id=file_id,
                message_id=message_id,
                channel_id=channel_id,
                user_id=user_id,
                filename=file_info.get("name", "unknown"),
                title=file_info.get("title"),
                mimetype=file_info.get("mimetype"),
                filetype=file_info.get("filetype"),
                size=file_info.get("size", 0),
                slack_url=file_info.get("url_private"),
                slack_created_at=datetime.fromtimestamp(file_info.get("created", 0)),
                expires_at=datetime.utcnow() + timedelta(days=30)
            )

        # Check file size limit (security measure)
        max_size_bytes = settings.max_file_size_mb * 1024 * 1024
        if file_record.size > max_size_bytes:
            logger.warning(
                "file_too_large",
                file_id=file_id,
                filename=file_record.filename,
                size_mb=file_record.size / (1024 * 1024),
                max_mb=settings.max_file_size_mb
            )
            file_record.processing_error = f"File exceeds maximum size of {settings.max_file_size_mb}MB"
            file_record.is_processed = 1
            await db.commit()
            return file_record

        # Log file info for debugging
        logger.info(
            "processing_file",
            file_id=file_id,
            filename=file_record.filename,
            filetype=file_record.filetype,
            size=file_record.size,
            has_url=bool(file_record.slack_url)
        )

        if not existing:
            db.add(file_record)

        await db.commit()
        await db.refresh(file_record)

        # Download file from Slack
        try:
            file_data = await slack_client_manager.download_file(file_record.slack_url)

            if not file_data:
                file_record.processing_error = "Failed to download from Slack"
                await db.commit()
                return file_record

            # Store original file in S3
            s3_key = f"files/{file_id}/original"
            success = storage_manager.upload_file(
                file_data,
                s3_key,
                content_type=file_record.mimetype
            )

            if success:
                file_record.s3_key = s3_key
                file_record.is_downloaded = 1

            # Extract text content based on file type
            extracted_text = await self._extract_text(
                file_data,
                file_record.filetype,
                file_record.mimetype
            )

            if extracted_text:
                # Store extracted text in S3
                text_key = f"files/{file_id}/extracted_text"
                storage_manager.upload_file(
                    extracted_text.encode('utf-8'),
                    text_key,
                    content_type='text/plain'
                )

                file_record.extracted_text = extracted_text[:10000]  # Store preview in DB
                file_record.s3_text_key = text_key

                # Queue for embedding generation (send file_id only, not full text to avoid message size issues)
                logger.info("queueing_file_for_embedding", file_id=file_id, text_length=len(extracted_text))
                publish_success = await queue_manager.publish(
                    queue=queue_manager.EMBEDDINGS_QUEUE,
                    message={
                        "type": "file",
                        "file_id": file_id,
                        "channel_id": channel_id
                    }
                )
                if publish_success:
                    logger.info("file_queued_for_embedding", file_id=file_id)
                else:
                    logger.error("failed_to_queue_embedding", file_id=file_id)

            file_record.is_processed = 1
            await db.commit()

            logger.info(
                "file_processed",
                file_id=file_id,
                filetype=file_record.filetype,
                has_text=bool(extracted_text)
            )

            return file_record

        except Exception as e:
            logger.error("file_processing_error", file_id=file_id, error=str(e))
            file_record.processing_error = str(e)
            await db.commit()
            return file_record

    async def _extract_text(
        self,
        file_data: bytes,
        filetype: Optional[str],
        mimetype: Optional[str]
    ) -> Optional[str]:
        """Extract text content from file based on type"""

        if not filetype:
            return None

        try:
            # Plain text files
            if filetype in self.TEXT_EXTRACTABLE:
                return file_data.decode('utf-8', errors='ignore')

            # PDF files
            if filetype in self.PDF_TYPES:
                return await self._extract_pdf_text(file_data)

            # Images (OCR in future phases)
            if filetype in self.IMAGE_TYPES:
                logger.info("image_extraction_skipped", filetype=filetype)
                return None

            # Office documents (implement in future if needed)
            if filetype in self.DOC_TYPES:
                logger.info("doc_extraction_skipped", filetype=filetype)
                return None

            return None

        except Exception as e:
            logger.error("text_extraction_error", filetype=filetype, error=str(e))
            return None

    async def _extract_pdf_text(self, file_data: bytes) -> Optional[str]:
        """Extract text from PDF file"""
        try:
            # Use pypdf for basic text extraction
            # Will require: pip install pypdf
            import pypdf

            pdf_file = io.BytesIO(file_data)
            reader = pypdf.PdfReader(pdf_file)

            text_parts = []
            for page_num, page in enumerate(reader.pages):
                text = page.extract_text()
                text_parts.append(f"[Page {page_num + 1}]\n{text}")

            return "\n\n".join(text_parts)

        except ImportError:
            logger.warning("pypdf_not_installed")
            return None
        except Exception as e:
            logger.error("pdf_extraction_error", error=str(e))
            return None


# Global file processor instance
file_processor = FileProcessor()
