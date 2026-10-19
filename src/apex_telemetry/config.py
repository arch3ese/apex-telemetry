from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application runtime configuration and environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENVIRONMENT: Literal["development", "testing", "production"] = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    PROJECT_NAME: str = "ApexTelemetry Platform"
    API_V1_PREFIX: str = "/api/v1"

    # Database URLs
    # Example: postgresql+asyncpg://user:pass@localhost:5432/dbname
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://apex_user:apex_password@localhost:5432/apex_telemetry",
        description="Async connection string for FastAPI application runtime",
    )
    SYNC_DATABASE_URL: str = Field(
        default="postgresql+psycopg://apex_user:apex_password@localhost:5432/apex_telemetry",
        description="Synchronous connection string for Alembic schema migrations",
    )

    # Database Connection Pool Configuration
    DB_POOL_SIZE: int = Field(default=10, ge=1, le=50)
    DB_MAX_OVERFLOW: int = Field(default=20, ge=0, le=100)
    DB_POOL_TIMEOUT: int = Field(default=30, ge=1, le=120)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton instance of application settings."""
    return Settings()
