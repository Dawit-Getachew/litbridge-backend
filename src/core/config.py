"""Application settings loaded from environment variables."""

import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for LitBridge services."""

    APP_NAME: str
    DEBUG: bool
    HOST: str
    PORT: int
    DATABASE_URL: str
    REDIS_URL: str
    NCBI_API_KEY: str
    CONTACT_EMAIL: str
    LLM_PROVIDER: str
    OPENAI_API_KEY: str
    OPENAI_MODEL: str
    OPENROUTER_API_KEY: str
    OPENROUTER_MODEL: str
    SEMANTIC_SCHOLAR_API_KEY: str = ""
    CORS_ORIGINS: str = "*"
    SECRET_KEY: str
    CHAT_MAX_HISTORY_TURNS: int = 10
    CHAT_MAX_CONTEXT_RECORDS: int = 25

    SENDGRID_API_KEY: str = ""
    SENDGRID_FROM_EMAIL: str = ""
    SENDGRID_FROM_NAME: str = "LitBridge"
    ADMIN_EMAIL: str = ""

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    OTP_EXPIRE_SECONDS: int = 300
    OTP_MAX_ATTEMPTS: int = 5

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS_ORIGINS from string to list. Accepts '*', JSON array, or comma-separated."""
        v = self.CORS_ORIGINS.strip()
        if v.startswith("["):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                pass
        return [origin.strip() for origin in v.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def llm_base_url(self) -> str:
        """Resolve chat completions base URL from selected provider."""
        if self.LLM_PROVIDER == "openrouter":
            return "https://openrouter.ai/api/v1"
        return "https://api.openai.com/v1"

    @property
    def llm_api_key(self) -> str:
        """Resolve API key from selected provider."""
        if self.LLM_PROVIDER == "openrouter":
            return self.OPENROUTER_API_KEY
        return self.OPENAI_API_KEY

    @property
    def llm_model(self) -> str:
        """Resolve model name from selected provider."""
        if self.LLM_PROVIDER == "openrouter":
            return self.OPENROUTER_MODEL
        return self.OPENAI_MODEL


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
