"""Code snippet model for storing extracted code blocks"""
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import Column, String, Text, Integer, BigInteger, DateTime, JSON, Index, ForeignKey
from sqlalchemy.dialects.mysql import LONGTEXT
from app.core.database import Base


class CodeSnippet(Base):
    """Stores extracted and parsed code snippets from Slack messages"""

    __tablename__ = "code_snippets"

    # Primary key
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Identifiers
    snippet_id = Column(String(100), unique=True, nullable=False, index=True)
    message_id = Column(String(50), ForeignKey('messages.message_id', ondelete='CASCADE'), nullable=False, index=True)
    snippet_index = Column(Integer, default=0, nullable=False)  # Multiple snippets per message

    # Code metadata
    language = Column(String(50), nullable=True, index=True)  # python, javascript, go, etc.
    code_type = Column(String(50), nullable=True, index=True)  # function, class, import, inline

    # Code content
    raw_code = Column(LONGTEXT, nullable=False)

    # Extracted metadata from AST parsing
    extracted_metadata = Column(JSON, nullable=True)
    # Schema:
    # {
    #     "function_name": "authenticate_user",
    #     "class_name": "UserAuth",
    #     "parameters": ["username", "password"],
    #     "return_type": "bool",
    #     "imports": ["hashlib", "jwt"],
    #     "decorators": ["@app.post", "@require_auth"],
    #     "docstring": "Authenticate user with credentials",
    #     "complexity_score": 3.5,
    #     "line_count": 15,
    #     "has_docstring": true,
    #     "calls": ["hash_password", "verify_token"]
    # }

    # Vector embedding reference
    vector_id = Column(String(100), nullable=True, unique=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Indexes for performance
    __table_args__ = (
        Index('idx_code_message_id', 'message_id'),
        Index('idx_code_language', 'language'),
        Index('idx_code_type', 'code_type'),
        Index('idx_code_snippet_id', 'snippet_id'),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "snippet_id": self.snippet_id,
            "message_id": self.message_id,
            "snippet_index": self.snippet_index,
            "language": self.language,
            "code_type": self.code_type,
            "raw_code": self.raw_code,
            "extracted_metadata": self.extracted_metadata or {},
            "vector_id": self.vector_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }