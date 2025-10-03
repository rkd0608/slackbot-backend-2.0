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

logger = get_logger(__name__)


class FileProcessor:
    """Processes Slack files - download, extract content, store"""

    # Supported file types for text extraction
    TEXT_EXTRACTABLE = {
        'text', 'python', 'javascript', 'java', 'go', 'rust',
        'markdown', 'json', 'xml', 'yaml', 'csv', 'sql'
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
        db: AsyncSession
    ) -> Optional[File]:
        """Process a file from Slack"""

        file_id = file_info.get("id")

        # Check if already processed
        result = await db.execute(
            select(File).where(File.file_id == file_id)
        )
        existing = result.scalar_one_or_none()

        if existing and existing.is_processed:
            logger.info("file_already_processed", file_id=file_id)
            return existing

        # Create or update file record
        file_record = existing or File(
            file_id=file_id,
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

                # Queue for embedding generation
                queue_manager.publish(
                    queue=queue_manager.EMBEDDINGS_QUEUE,
                    message={
                        "type": "file",
                        "file_id": file_id,
                        "text": extracted_text,
                        "channel_id": channel_id
                    }
                )

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
