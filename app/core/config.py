"""Application configuration using Pydantic Settings"""
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

    # Slack Configuration
    slack_bot_token: str = Field(..., min_length=1)
    slack_app_token: str = Field(..., min_length=1)
    slack_signing_secret: str = Field(..., min_length=1)
    slack_client_id: str = Field(..., min_length=1)
    slack_client_secret: str = Field(..., min_length=1)

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

    # Rate Limiting
    query_rate_limit_per_hour: int = Field(default=100)
    query_burst_limit_per_minute: int = Field(default=10)


# Singleton instance
settings = Settings()
