"""Validated application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime configuration for the Auto-Data-Extractor microservice."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Service metadata & HTTP server
    SERVICE_NAME: str = "auto-data-extractor"
    SERVICE_VERSION: str = "1.0.0"
    ENV: Literal["development", "production", "test"] = "development"
    HOST: str = "0.0.0.0"
    PORT: int = Field(default=5010, ge=1, le=65535)
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5005"

    # Security & Auth (compatible with resto-orbit-backend JWT)
    JWT_SECRET: str = Field(
        default="dev_super_secret_jwt_key_2026",
        min_length=8,
    )
    JWT_ALGORITHM: str = "HS256"
    INTERNAL_API_KEY: str = Field(default="dev_internal_extractor_key", min_length=8)

    # PostgreSQL shared database
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/resto-orbit-test"
    DB_POOL_MIN_SIZE: int = Field(default=2, ge=1, le=50)
    DB_POOL_MAX_SIZE: int = Field(default=15, ge=2, le=100)
    DB_CONNECT_TIMEOUT: int = Field(default=5, ge=1, le=60)

    # Optional Core Backend HTTP URL
    RESTO_ORBIT_BACKEND_URL: str = "http://localhost:5005/api/v1"

    # IMAP Email Inbox Ingestion
    IMAP_ENABLED: bool = False
    IMAP_HOST: str = "imap.gmail.com"
    IMAP_PORT: int = Field(default=993, ge=1, le=65535)
    IMAP_USE_SSL: bool = True
    IMAP_USER: str = ""
    IMAP_PASSWORD: str = ""
    IMAP_MAILBOX: str = "INBOX"
    IMAP_SEARCH_CRITERIA: str = "UNSEEN"
    IMAP_SENDER_FILTER: str = ""
    IMAP_SUBJECT_FILTER: str = ""
    IMAP_MARK_AS_READ: bool = True
    IMAP_MOVE_PROCESSED_FOLDER: str = ""

    # Background Scheduler
    POLL_INTERVAL_SECONDS: int = Field(default=120, ge=10, le=86400)
    POLL_MAX_MESSAGES_PER_RUN: int = Field(default=25, ge=1, le=500)

    # Extraction & Import Controls
    MAX_PDF_SIZE_MB: int = Field(default=15, ge=1, le=100)
    DEFAULT_SHIFT_TYPE: Literal["Shift 1", "Shift 2"] = "Shift 2"
    AUTO_IMPORT_ENABLED: bool = True
    OVERWRITE_SUBMITTED_SHIFTS: bool = False

    @field_validator("CORS_ORIGINS")
    @classmethod
    def normalize_cors(cls, value: str) -> str:
        return value.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def max_pdf_size_bytes(self) -> int:
        return self.MAX_PDF_SIZE_MB * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return cached singleton Settings instance."""
    return Settings()
