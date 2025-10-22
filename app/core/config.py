"""Application configuration using Pydantic Settings"""
import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Application Settings
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    workers: int = Field(default=4)
    app_base_url: str = Field(default="http://localhost:8000")
    frontend_url: str = Field(default="http://localhost:3001")  # Next.js frontend URL

    # Secrets Management
    use_secrets_manager: bool = Field(default=False)  # Enable AWS Secrets Manager in production
    aws_secret_name: str = Field(default="slackbot/production")  # Name of secret in AWS Secrets Manager

    # Slack Configuration
    slack_bot_token: str = Field(..., min_length=1)
    slack_app_token: str = Field(..., min_length=1)
    slack_signing_secret: str = Field(..., min_length=1)
    slack_client_id: str = Field(..., min_length=1)
    slack_client_secret: str = Field(..., min_length=1)
    slack_bot_user_id: Optional[str] = Field(default=None)  # Bot's user ID for filtering

    # Stripe Configuration (optional - set these when enabling billing)
    stripe_secret_key: Optional[str] = Field(default=None)
    stripe_publishable_key: Optional[str] = Field(default=None)
    stripe_webhook_secret: Optional[str] = Field(default=None)
    stripe_price_starter: Optional[str] = Field(default=None)  # Price ID for starter tier
    stripe_price_growth: Optional[str] = Field(default=None)  # Price ID for growth tier
    stripe_price_business: Optional[str] = Field(default=None)  # Price ID for business tier
    stripe_price_enterprise: Optional[str] = Field(default=None)  # Price ID for enterprise tier

    # Email Configuration (AWS SES) - optional for development
    email_sender_address: Optional[str] = Field(default=None)  # Verified SES email
    email_sender_name: str = Field(default="Slack AI Assistant")

    # MySQL Configuration
    mysql_host: str = Field(default="localhost")
    mysql_port: int = Field(default=3306)
    mysql_user: str = Field(..., min_length=1)
    mysql_password: str = Field(..., min_length=1)
    mysql_database: str = Field(..., min_length=1)

    @property
    def mysql_url(self) -> str:
        """Construct MySQL connection URL"""
        return f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"

    @property
    def oauth_redirect_uri(self) -> str:
        """Construct OAuth redirect URI from app base URL"""
        return f"{self.app_base_url}/oauth/callback"

    # Pinecone Configuration
    pinecone_api_key: str = Field(..., min_length=1)
    pinecone_environment: str = Field(default="us-east-1-aws")
    pinecone_index_name: str = Field(default="slack-embeddings")

    # RabbitMQ Configuration
    rabbitmq_host: str = Field(default="localhost")
    rabbitmq_port: int = Field(default=5672)
    rabbitmq_user: str = Field(default="guest")
    rabbitmq_password: str = Field(default="guest")
    rabbitmq_vhost: str = Field(default="/")

    @property
    def rabbitmq_url(self) -> str:
        """Construct RabbitMQ connection URL"""
        return f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}@{self.rabbitmq_host}:{self.rabbitmq_port}/{self.rabbitmq_vhost}"

    # AWS S3 Configuration
    aws_access_key_id: str = Field(..., min_length=1)
    aws_secret_access_key: str = Field(..., min_length=1)
    aws_region: str = Field(default="us-east-1")
    aws_s3_bucket: str = Field(..., min_length=1)

    # Redis Configuration
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_password: Optional[str] = Field(default=None)
    redis_db: int = Field(default=0)

    @property
    def redis_url(self) -> str:
        """Construct Redis connection URL"""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # OpenAI Configuration
    openai_api_key: str = Field(..., min_length=1)
    openai_embedding_model: str = Field(default="text-embedding-3-large")
    openai_llm_model: str = Field(default="gpt-4-turbo")

    # Security
    jwt_secret_key: str = Field(..., min_length=32)
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiration_minutes: int = Field(default=1440)

    # Performance Settings
    max_context_tokens: int = Field(default=150000)
    embedding_batch_size: int = Field(default=100)
    retrieval_candidates: int = Field(default=200)
    rerank_top_k: int = Field(default=50)
    cache_ttl_seconds: int = Field(default=300)

    # Query Enhancement Features
    enable_query_rewriting: bool = Field(default=True)
    query_rewriter_model: str = Field(default="gpt-3.5-turbo")
    query_rewriter_max_history: int = Field(default=6)  # Last 6 messages (3 turns)
    enable_llm_entity_extraction: bool = Field(default=False)  # Expensive, use regex by default
    enable_query_expansion: bool = Field(default=True)  # Generate synonym variations for better recall
    enable_llm_query_analysis: bool = Field(default=True)  # Use GPT-4 for comprehensive query analysis
    query_analyzer_model: str = Field(default="gpt-4o-mini")  # Model for query analysis

    # Rate Limiting
    query_rate_limit_per_hour: int = Field(default=100)
    query_burst_limit_per_minute: int = Field(default=10)

    # File Upload Security
    max_file_size_mb: int = Field(default=50)  # Maximum file size in MB

    # Code Intelligence Settings
    enable_code_intelligence: bool = Field(default=True)
    code_embedding_provider: str = Field(default="voyage")  # voyage|openai
    code_embedding_model: str = Field(default="voyage-code-2")
    code_embedding_dimension: int = Field(default=1536)
    voyage_api_key: Optional[str] = Field(default=None)  # Voyage AI API key

    # Pinecone namespace strategy
    pinecone_code_namespace: str = Field(default="code")  # Separate namespace for code

    # Language support
    supported_code_languages: list = Field(default_factory=lambda: [
        "python", "javascript", "typescript", "java",
        "go", "rust", "cpp", "csharp", "ruby", "php", "sql"
    ])

    # AST parsing settings
    enable_ast_parsing: bool = Field(default=True)
    max_code_snippet_length: int = Field(default=10000)  # characters
    min_code_snippet_lines: int = Field(default=1)  # Capture all code, even single lines

    # Code retrieval settings
    code_search_weight: float = Field(default=0.6)  # Weight for code vs text search
    enable_structural_matching: bool = Field(default=True)
    code_search_min_score: float = Field(default=0.0)  # Minimum similarity score (0.0 = no filtering)

    # GitHub Integration
    github_client_id: Optional[str] = Field(default=None)
    github_client_secret: Optional[str] = Field(default=None)
    github_oauth_redirect_uri: Optional[str] = Field(default=None)
    github_api_base_url: str = Field(default="https://api.github.com")
    github_api_version: str = Field(default="2022-11-28")
    pinecone_github_index_name: str = Field(default="github-code-embeddings")

    # Token Encryption (for OAuth tokens)
    oauth_token_encryption_key: str = Field(..., min_length=1)  # Base64-encoded 32-byte key

    # Code Embedding Settings
    enable_dual_embedding: bool = Field(default=True)
    code_embedding_weight: float = Field(default=0.6)
    semantic_embedding_weight: float = Field(default=0.4)

    # Chunking Settings for Large Files
    max_file_size_for_chunking: int = Field(default=10000)  # characters
    max_lines_for_chunking: int = Field(default=500)  # lines

    # Code Intelligence Cache
    cache_code_structure: bool = Field(default=True)
    code_structure_cache_ttl: int = Field(default=86400)  # 24 hours


# Singleton instance
settings = Settings()
