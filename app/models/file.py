"""File model for Slack files"""
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, BigInteger, DateTime, JSON, Index
from app.core.database import Base


class File(Base):
    """Slack file metadata and storage references"""

    __tablename__ = "files"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Slack identifiers
    file_id = Column(String(50), unique=True, nullable=False, index=True)
    message_id = Column(String(50), nullable=True, index=True)  # Associated message
    channel_id = Column(String(50), nullable=False, index=True)
    user_id = Column(String(50), nullable=False)

    # File properties
    filename = Column(String(500), nullable=False)
    title = Column(String(500), nullable=True)
    mimetype = Column(String(100), nullable=True)
    filetype = Column(String(50), nullable=True)
    size = Column(BigInteger, default=0)

    # URLs
    slack_url = Column(String(1000), nullable=True)
    s3_key = Column(String(500), nullable=True)  # S3 storage key

    # Extracted content
    extracted_text = Column(Text, nullable=True)
    s3_text_key = Column(String(500), nullable=True)  # S3 key for extracted text

    # Processing status
    is_downloaded = Column(Integer, default=0)
    is_processed = Column(Integer, default=0)
    processing_error = Column(Text, nullable=True)

    # Embedding reference
    vector_id = Column(String(100), nullable=True)

    # Timestamps
    slack_created_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)  # For lifecycle management

    __table_args__ = (
        Index('idx_channel_created', 'channel_id', 'created_at'),
        Index('idx_filetype', 'filetype'),
    )
