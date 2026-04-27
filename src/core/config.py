"""Application settings loaded from environment variables."""

import json
from functools import lru_cache
from typing import Literal

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

    RANKING_PUBMED_WEIGHT: float = 2.5
    """Multiplicative weight applied to PubMed's RRF contribution. PubMed's
    LambdaMART Best Match has the highest signal-to-noise of any source we
    query, and is the gold standard reviewers benchmark against. 2.5 biases
    PubMed strongly up for free-text queries without letting it completely
    dominate — two consensus hits on Europe PMC + OpenAlex can still
    outrank a single low-ranked PubMed hit. Set to 1.0 to fall back to
    plain RRF."""

    RANKING_EUROPEPMC_WEIGHT: float = 1.0
    """Multiplicative weight applied to Europe PMC's RRF contribution.
    Europe PMC indexes full text and has strong synonym/MeSH expansion,
    so its top hits are usually high quality but less curated than
    PubMed. 1.0 is the neutral RRF baseline."""

    RANKING_OPENALEX_WEIGHT: float = 0.5
    """Multiplicative weight applied to OpenAlex's RRF contribution.
    OpenAlex has excellent coverage and citation counts but its
    doc-type metadata has known noise (>300k misclassifications vs WoS
    per 2024 arXiv audit). Down-weighting to 0.5 keeps its breadth
    while reducing its influence on ordering. Set to 1.0 to treat
    OpenAlex on par with Europe PMC."""

    RANKING_CTGOV_WEIGHT: float = 0.0
    """Multiplicative weight applied to ClinicalTrials.gov's RRF
    contribution for FREE queries. CT.gov records are protocols, not
    peer-reviewed literature, so they shouldn't compete with journal
    articles in free-text relevance ranking. They still surface for
    users who explicitly include CT.gov in the source filter, just not
    ordered against articles. Set to >0 to include in fusion."""

    RANKING_TITLE_BOOST: float = 0.3
    """Maximum multiplicative title-match lift (alpha). A cluster whose
    winner title contains every meaningful query term is multiplied by
    1 + alpha = 1.3; partial matches scale linearly. Set to 0.0 to
    disable the title boost while keeping RRF and recency."""

    RANKING_ABSTRACT_BOOST: float = 0.15
    """Maximum multiplicative abstract-match lift. A cluster whose
    winner abstract contains every meaningful query term is multiplied
    by 1 + 0.15 = 1.15. Weighted roughly half of the title boost so
    title matches still dominate — same ratio Google Scholar uses.
    Set to 0.0 to disable abstract matching."""

    RANKING_RECENCY_BOOST: float = 0.1
    """Maximum multiplicative recency lift (beta). A current-year paper
    is multiplied by 1 + beta = 1.1; the bonus drops linearly to 1.0 over
    5 years and stays at 1.0 thereafter. Small enough that an excellent
    older paper can still outrank a mediocre recent commentary."""

    RANKING_CITATION_BOOST: float = 0.25
    """Maximum multiplicative citation-count lift. Citation count is
    Google Scholar's #1 ranking signal — heavily cited work tends to be
    more relevant. Applied as 1 + boost * log1p(citations) / 8, then
    capped by RANKING_CITATION_CAP. Decays logarithmically so a 10x
    citation gap only translates to a modest ranking shift, preventing
    a single highly-cited seminal paper from dominating a niche query."""

    RANKING_CITATION_CAP: float = 0.40
    """Absolute ceiling on the citation-count contribution, expressed
    as a multiplicative lift. With cap=0.40 a paper with infinite
    citations is still only worth 1.40x — this prevents seminal
    mega-papers from swamping fresh relevant work on a specific query.
    Set higher (e.g. 0.80) to reward citations more aggressively."""

    RANKING_VERSION: str = "v3"
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

    RANKING_LLM_REWRITE: bool = True
    """When True, expand the user's query into per-source rewrites via
    the configured LLM before adapter translation. Gated by query type
    and search mode — the translator only invokes the rewriter for
    ``QueryType.FREE`` + ``SearchMode.QUICK`` (the exact case where a
    fast, synthesized answer matters most). BOOLEAN / PICO / Deep modes
    skip the rewriter so PRISMA workflows and agentic Deep paths remain
    fully deterministic. Adds ~0.5–2s of first-hit latency (cached 24h
    by query hash). Falls back silently to deterministic adapters on
    any LLM error or timeout; search latency cannot regress catastrophically."""

    RANKING_LLM_REWRITE_TTL_SECONDS: int = 86400
    """Redis TTL for cached LLM query rewrites. 24h is a safe default —
    the rewrite depends only on the raw query text, not on external data."""

    RANKING_LLM_REWRITE_TIMEOUT_SECONDS: float = 3.0
    """Hard budget for the LLM rewrite step. Exceeding this cancels the
    rewrite and continues with the standard adapter translation path so
    search latency never regresses catastrophically."""

    RANKING_QUERY2DOC_ENABLED: bool = True
    """When True and the LLM rewriter runs (gated by FREE+Quick), also
    request a short pseudo-document from the model — a 3-4 sentence
    synthetic abstract — and append its highest-signal terms to each
    per-source query via OR joins. This Query2doc-style expansion is
    proven to lift biomedical retrieval nDCG by 10-25% on NFCorpus,
    TREC-COVID, and SciFact. Shares the rewriter's budget and cache;
    silent no-op on any LLM failure."""

    # Phase B — Local BM25 reranker. Runs over the already-fused cluster
    # list using the bm25s library (~50 ms for 200 docs, no torch). The
    # result is blended with the weighted RRF score; set weight=0 to disable.

    RANKING_BM25_WEIGHT: float = 0.35
    """Blend weight for the local BM25 reranker. Final score is
    ``w * bm25_normalized + (1 - w) * rrf_score``. 0.0 disables BM25
    entirely (pure RRF); 0.35 is the midpoint TREC PM 2020 found
    optimal on biomedical hybrid retrieval (0.3–0.5 range). Above
    ~0.6 the local lexical scorer starts dominating over multi-source
    consensus, which is usually undesirable."""

    RANKING_BM25_TOP_K: int = 200
    """Upper bound on the number of clusters the BM25 reranker scores.
    Set high enough that the first page of deep-mode results benefits;
    set low enough that indexing per call stays under ~50ms. 200 is
    a safe default for our typical result sizes."""

    # Phase D — optional MedCPT cross-encoder. Off by default to keep
    # Docker images lean. When enabled, activates only for FREE+Quick so
    # other paths stay fully deterministic.

    RANKING_MEDCPT: bool = False
    """Master on/off for the NCBI MedCPT cross-encoder reranker. When
    True and the request is FREE+Quick, the top RANKING_MEDCPT_TOP_K
    clusters are re-ordered by MedCPT's (query, title+abstract) score
    — a model distilled from 255M PubMed click logs. Flag is off by
    default so the base image doesn't need ML weights; enable after
    exporting ONNX weights to the configured path."""

    RANKING_MEDCPT_BACKEND: Literal["onnx", "sidecar", "hf_endpoints", "hf_api"] = "onnx"
    """Which backend to use for MedCPT inference. ``onnx`` (default)
    runs an INT8-quantized ONNX model inside the same process via
    onnxruntime (~60 ms / 100 candidates, ~450 MB RAM, no PyTorch).
    ``sidecar`` POSTs to a Coolify sidecar container. ``hf_endpoints``
    uses a Hugging Face Inference Endpoint URL. ``hf_api`` uses the
    pay-per-call HF serverless API. Lazy-imports keep dependencies
    tree-shaken per backend."""

    RANKING_MEDCPT_TOP_K: int = 100
    """Number of top clusters passed to the MedCPT cross-encoder.
    Matches the model's published eval top-K. Fewer means faster;
    more means better recall-at-depth at the cost of latency."""

    RANKING_MEDCPT_MODEL_PATH: str = "./models/medcpt-cross-onnx-qint8"
    """Filesystem path to the quantized ONNX weights for the MedCPT
    cross-encoder. Populated by ``scripts/export_medcpt_onnx.py``;
    bundled into the image when the Dockerfile is built with
    ``INSTALL_MEDCPT=true``."""

    HF_MEDCPT_ENDPOINT_URL: str = ""
    """Hugging Face Inference Endpoint URL for MedCPT. Only read when
    ``RANKING_MEDCPT_BACKEND=hf_endpoints``."""

    HF_API_TOKEN: str = ""
    """Hugging Face API token for the ``hf_api`` and ``hf_endpoints``
    MedCPT backends. Only read when those backends are selected."""

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
