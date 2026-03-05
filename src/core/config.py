"""Application settings loaded from environment variables."""

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
    CORS_ORIGINS: list[str]
    SECRET_KEY: str
    CHAT_MAX_HISTORY_TURNS: int = 10
    CHAT_MAX_CONTEXT_RECORDS: int = 25

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
