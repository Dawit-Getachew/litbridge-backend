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

    # Cross-source ranking knobs (Phase 2 — weighted Reciprocal Rank Fusion).
    # Defaults are deliberately conservative; safe to deploy without env edits.
    # See `src/services/dedup_service.py` for the formulas these feed into.
    RANKING_RRF_K: int = 60
    """Reciprocal Rank Fusion smoothing constant. Lower values (e.g. 20)
    let the top of each source's list dominate; higher values (e.g. 120)
    flatten contributions and reward consensus across sources. 60 is the
    canonical Cormack et al. (2009) default."""

    RANKING_PUBMED_WEIGHT: float = 1.3
    """Multiplicative weight applied to PubMed's RRF contribution. PubMed's
    LambdaMART Best Match has higher signal-to-noise than full-text-indexed
    sources; 1.3 is a small bias that nudges PubMed up without single-source
    dominance. Set to 1.0 to fall back to plain RRF."""

    RANKING_TITLE_BOOST: float = 0.3
    """Maximum multiplicative title-match lift (alpha). A cluster whose
    winner title contains every meaningful query term is multiplied by
    1 + alpha = 1.3; partial matches scale linearly. Set to 0.0 to
    disable the title boost while keeping RRF and recency."""

    RANKING_RECENCY_BOOST: float = 0.1
    """Maximum multiplicative recency lift (beta). A current-year paper
    is multiplied by 1 + beta = 1.1; the bonus drops linearly to 1.0 over
    5 years and stays at 1.0 thereafter. Small enough that an excellent
    older paper can still outrank a mediocre recent commentary."""

    RANKING_VERSION: str = "v2"
    """Embedded in Redis cache keys for search results. Bump this string
    whenever the ranking algorithm changes meaningfully so stale entries
    cannot mask the new ordering. Reverting to a prior value is an
    instant rollback path (no redeploy required)."""

    # Phase 3 — optional advanced ranking. Both features default OFF so that
    # upgrading to a release that ships them is a pure no-op for existing
    # deployments; turn them on via env vars only after validation.

    RANKING_MMR_LAMBDA: float = 1.0
    """Maximal Marginal Relevance trade-off. lambda=1.0 disables MMR (pure
    relevance order — Phase 2 behavior). lambda=0.7 balances relevance and
    diversity over title-token Jaccard similarity; lower values diversify
    more aggressively. Applied after RRF+boost sort, before the final
    deduped list is returned. Must be in [0.0, 1.0]."""

    RANKING_MMR_K: int = 50
    """Number of top clusters to rerank with MMR. Matches the typical
    first-page size so diversification affects what users actually see
    while leaving the long tail in relevance order."""

    RANKING_LLM_REWRITE: bool = False
    """When True and query_type=FREE, expand the user's query into a
    per-source rewrite via the configured LLM before adapter translation.
    Adds ~0.5–2s of latency per new query (cached for 24h by query hash),
    so default is False to preserve the fast-path SLA. The rewrite is
    server-side only — there is NO new request field, so the frontend
    cannot accidentally toggle it. Falls back silently to the standard
    adapters on any LLM error or timeout."""

    RANKING_LLM_REWRITE_TTL_SECONDS: int = 86400
    """Redis TTL for cached LLM query rewrites. 24h is a safe default —
    the rewrite depends only on the raw query text, not on external data."""

    RANKING_LLM_REWRITE_TIMEOUT_SECONDS: float = 3.0
    """Hard budget for the LLM rewrite step. Exceeding this cancels the
    rewrite and continues with the standard adapter translation path so
    search latency never regresses catastrophically."""

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
