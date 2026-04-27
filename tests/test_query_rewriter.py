"""Tests for the LLM query rewriter (Phase 3 + v3 Query2doc expansion).

In v3 the rewriter is:

* Default ON (``RANKING_LLM_REWRITE=True``).
* Runtime-gated: invoked only for ``QueryType.FREE`` +
  ``SearchMode.QUICK``. Other combinations skip the LLM entirely.
* An exact no-op when explicitly disabled via the env flag.
* Strictly non-regressing on failure — adapter output must win whenever
  the LLM times out, errors, or returns malformed JSON.
* Cached in Redis for ``RANKING_LLM_REWRITE_TTL_SECONDS`` so repeat
  traffic does not re-invoke the model.
* Extended with a Query2doc pseudo-abstract expansion when
  ``RANKING_QUERY2DOC_ENABLED`` is True.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.ai.adapters import translate_for_all_sources
from src.ai.query_rewriter import rewrite_for_sources
from src.core.config import get_settings
from src.schemas.enums import QueryType, SearchMode, SourceType


class _FakeResponse:
    """Minimal httpx.Response stand-in for LLMClient mocks."""

    def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpClient:
    """httpx.AsyncClient stand-in that returns a pre-canned chat reply."""

    def __init__(self, *, reply: str | None, status_code: int = 200) -> None:
        self.reply = reply
        self.status_code = status_code
        self.calls = 0

    async def post(self, url: str, **_kwargs: Any) -> _FakeResponse:  # noqa: ARG002
        self.calls += 1
        if self.reply is None:
            return _FakeResponse(status_code=self.status_code, payload={})
        return _FakeResponse(
            status_code=self.status_code,
            payload={"choices": [{"message": {"content": self.reply}}]},
        )


class _FakeLLMClient:
    """LLMClient-shaped stub exposing the attributes the rewriter uses."""

    def __init__(self, *, reply: str | None) -> None:
        self.model = "test-model"
        self.base_url = "https://llm.invalid/v1"
        self.api_key = "not-a-real-key"
        self.client = _FakeHttpClient(reply=reply)
        self.settings = type("S", (), {"LLM_PROVIDER": "openai"})()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer test"}

    @staticmethod
    def _extract_message_content(payload: dict[str, Any]) -> str | None:
        choices = payload.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        return None


class _InMemoryRedis:
    """Tiny async Redis substitute sufficient for rewriter cache tests."""

    def __init__(self) -> None:
        self.store: dict[str, bytes | str] = {}
        self.gets = 0
        self.sets = 0

    async def get(self, key: str) -> bytes | None:
        self.gets += 1
        value = self.store.get(key)
        if value is None:
            return None
        return value.encode("utf-8") if isinstance(value, str) else value

    async def set(self, key: str, value: bytes | str, ex: int | None = None) -> bool:  # noqa: ARG002
        self.sets += 1
        self.store[key] = value
        return True


@pytest.mark.asyncio
async def test_rewriter_can_be_fully_disabled_via_env_flag() -> None:
    """Explicit ``RANKING_LLM_REWRITE=False`` must skip the LLM entirely —
    the env flag stays a hard kill-switch regardless of the v3 defaults."""
    llm = _FakeLLMClient(reply="""{"pubmed": "SHOULD-NOT-APPEAR"}""")
    redis_client = _InMemoryRedis()
    settings = get_settings().model_copy(update={"RANKING_LLM_REWRITE": False})

    translated = await translate_for_all_sources(
        query="impact of GLP-1 antagonists on high cholesterol",
        query_type=QueryType.FREE,
        search_mode=SearchMode.QUICK,
        sources=[SourceType.PUBMED, SourceType.OPENALEX],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )

    assert llm.client.calls == 0
    assert "SHOULD-NOT-APPEAR" not in translated[SourceType.PUBMED]
    assert "SHOULD-NOT-APPEAR" not in translated[SourceType.OPENALEX]


@pytest.mark.asyncio
async def test_rewriter_default_on_for_free_quick_mode() -> None:
    """With no env override, FREE + QUICK must invoke the rewriter — this
    is the v3 default and the exact case the client flagged (quick mode
    free-text search)."""
    reply = json.dumps(
        {
            "pubmed": "glp-1[tiab] AND cholesterol[tiab]",
            "openalex": "glp-1 cholesterol",
        },
    )
    llm = _FakeLLMClient(reply=reply)
    redis_client = _InMemoryRedis()
    settings = get_settings()  # default: RANKING_LLM_REWRITE=True

    translated = await translate_for_all_sources(
        query="GLP-1 antagonists and cholesterol",
        query_type=QueryType.FREE,
        search_mode=SearchMode.QUICK,
        sources=[SourceType.PUBMED, SourceType.OPENALEX],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )

    assert llm.client.calls == 1
    assert translated[SourceType.OPENALEX] == "glp-1 cholesterol"


@pytest.mark.asyncio
async def test_rewriter_skipped_for_deep_modes() -> None:
    """Deep modes have their own query synthesis; the rewriter must not
    invoke the LLM for them regardless of the flag default."""
    llm = _FakeLLMClient(reply="""{"pubmed": "never-used"}""")
    redis_client = _InMemoryRedis()
    settings = get_settings()  # default: flag True

    for mode in (
        SearchMode.DEEP_RESEARCH,
        SearchMode.DEEP_ANALYZE,
        SearchMode.DEEP_THINKING,
        SearchMode.LIGHT_THINKING,
    ):
        await translate_for_all_sources(
            query="metformin cardiovascular outcomes",
            query_type=QueryType.FREE,
            search_mode=mode,
            sources=[SourceType.PUBMED],
            llm_client=llm,  # type: ignore[arg-type]
            redis_client=redis_client,  # type: ignore[arg-type]
            settings=settings,
        )

    assert llm.client.calls == 0


@pytest.mark.asyncio
async def test_rewriter_enabled_overlays_rewrites_on_adapter_output() -> None:
    """When the flag is on and the LLM returns valid JSON rewrites, those
    strings must replace the adapter output for their respective sources."""
    reply = json.dumps(
        {
            "pubmed": "metformin[tiab] AND cardiovascular outcomes[tiab]",
            "europepmc": "metformin cardiovascular outcomes",
            "openalex": "metformin cardiovascular outcomes",
            "clinicaltrials": "metformin cardiovascular",
        },
    )
    llm = _FakeLLMClient(reply=reply)
    redis_client = _InMemoryRedis()
    settings = get_settings().model_copy(update={"RANKING_LLM_REWRITE": True})

    translated = await translate_for_all_sources(
        query="does metformin reduce cardiovascular outcomes?",
        query_type=QueryType.FREE,
        sources=[
            SourceType.PUBMED,
            SourceType.EUROPEPMC,
            SourceType.OPENALEX,
            SourceType.CLINICALTRIALS,
        ],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )

    assert translated[SourceType.PUBMED] == (
        "metformin[tiab] AND cardiovascular outcomes[tiab]"
    )
    assert translated[SourceType.EUROPEPMC] == "metformin cardiovascular outcomes"
    assert llm.client.calls == 1
    # Results are cached in Redis for subsequent callers.
    assert redis_client.sets == 1


@pytest.mark.asyncio
async def test_rewriter_only_runs_for_free_query_type() -> None:
    """BOOLEAN and PICO queries must never trigger the rewriter so PRISMA
    protocols and structured PICO workflows remain fully deterministic."""
    llm = _FakeLLMClient(reply="""{"pubmed": "should-never-be-used"}""")
    redis_client = _InMemoryRedis()
    settings = get_settings().model_copy(update={"RANKING_LLM_REWRITE": True})

    await translate_for_all_sources(
        query="(metformin[tiab] OR Metformin[MeSH]) AND stroke[tiab]",
        query_type=QueryType.BOOLEAN,
        sources=[SourceType.PUBMED, SourceType.OPENALEX],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )

    assert llm.client.calls == 0


@pytest.mark.asyncio
async def test_rewriter_uses_cache_on_second_call_with_same_query() -> None:
    """Repeat queries must hit the Redis cache instead of re-invoking the
    LLM, so post-warmup latency is identical to adapter-only translation."""
    reply = json.dumps({"pubmed": "cached pubmed rewrite"})
    llm = _FakeLLMClient(reply=reply)
    redis_client = _InMemoryRedis()
    settings = get_settings().model_copy(update={"RANKING_LLM_REWRITE": True})

    query = "chronic kidney disease biomarkers prognosis"

    first = await rewrite_for_sources(
        query=query,
        sources=[SourceType.PUBMED],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )
    second = await rewrite_for_sources(
        query=query,
        sources=[SourceType.PUBMED],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )

    assert first == {SourceType.PUBMED: "cached pubmed rewrite"}
    assert second == {SourceType.PUBMED: "cached pubmed rewrite"}
    # LLM was invoked exactly once across the two calls.
    assert llm.client.calls == 1


@pytest.mark.asyncio
async def test_rewriter_falls_back_when_llm_returns_malformed_json() -> None:
    """Invalid JSON from the model must not crash the search — the caller
    simply continues with deterministic adapter output."""
    llm = _FakeLLMClient(reply="this is not JSON at all")
    redis_client = _InMemoryRedis()
    settings = get_settings().model_copy(update={"RANKING_LLM_REWRITE": True})

    translated = await translate_for_all_sources(
        query="renal denervation resistant hypertension",
        query_type=QueryType.FREE,
        sources=[SourceType.PUBMED],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )

    assert translated[SourceType.PUBMED]  # adapter fallback is a non-empty string
    assert llm.client.calls == 1


@pytest.mark.asyncio
async def test_rewriter_falls_back_on_timeout() -> None:
    """Rewrites taking longer than ``RANKING_LLM_REWRITE_TIMEOUT_SECONDS``
    must abort cleanly and let the adapters take over, so worst-case
    search latency cannot exceed the timeout + adapter cost."""

    class _SlowHttpClient(_FakeHttpClient):
        async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            await asyncio.sleep(1.0)
            return await super().post(url, **kwargs)

    llm = _FakeLLMClient(reply="""{"pubmed": "never-used"}""")
    llm.client = _SlowHttpClient(reply="""{"pubmed": "never-used"}""")

    redis_client = _InMemoryRedis()
    settings = get_settings().model_copy(
        update={
            "RANKING_LLM_REWRITE": True,
            "RANKING_LLM_REWRITE_TIMEOUT_SECONDS": 0.05,
        },
    )

    translated = await translate_for_all_sources(
        query="hypertrophic cardiomyopathy imaging",
        query_type=QueryType.FREE,
        sources=[SourceType.PUBMED],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )

    # Adapter fallback wins because the rewriter timed out.
    assert "never-used" not in translated[SourceType.PUBMED]


# -----------------------------------------------------------------------------
# Query2doc expansion (v3 Phase C)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query2doc_appends_pseudo_doc_terms_when_enabled() -> None:
    """With Query2doc on, expansion terms from the pseudo-abstract must be
    appended to the per-source rewrite in the source's native syntax:

    * PubMed: ``(base) AND (term[tiab] OR ...)``
    * Europe PMC: ``(base) AND (term OR ...)``
    * OpenAlex / CT.gov: space-separated free-text.
    """
    reply = json.dumps(
        {
            "pubmed": "glp-1 antagonists cholesterol",
            "europepmc": "glp-1 antagonists cholesterol",
            "openalex": "glp-1 antagonists cholesterol",
            "clinicaltrials": "glp-1 cholesterol",
            "pseudo_doc": (
                "This trial evaluated glucagon-like-peptide-1 antagonists for "
                "patients with hyperlipidemia and elevated ldl cholesterol. "
                "Researchers measured cardiovascular outcomes and metabolic biomarkers."
            ),
        },
    )
    llm = _FakeLLMClient(reply=reply)
    redis_client = _InMemoryRedis()
    settings = get_settings().model_copy(
        update={
            "RANKING_LLM_REWRITE": True,
            "RANKING_QUERY2DOC_ENABLED": True,
        },
    )

    rewrites = await rewrite_for_sources(
        query="GLP-1 antagonists cholesterol",
        sources=[
            SourceType.PUBMED,
            SourceType.EUROPEPMC,
            SourceType.OPENALEX,
            SourceType.CLINICALTRIALS,
        ],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )

    pubmed_rewrite = rewrites[SourceType.PUBMED]
    assert pubmed_rewrite.startswith("(glp-1 antagonists cholesterol) AND (")
    assert "[tiab]" in pubmed_rewrite  # field-tag syntax preserved

    epmc_rewrite = rewrites[SourceType.EUROPEPMC]
    assert epmc_rewrite.startswith("(glp-1 antagonists cholesterol) AND (")
    assert "[tiab]" not in epmc_rewrite

    openalex_rewrite = rewrites[SourceType.OPENALEX]
    assert openalex_rewrite.startswith("glp-1 antagonists cholesterol ")


@pytest.mark.asyncio
async def test_query2doc_disabled_leaves_rewrites_untouched() -> None:
    """With the flag off, even a valid ``pseudo_doc`` in the LLM reply must
    not mutate the per-source rewrites."""
    reply = json.dumps(
        {
            "pubmed": "original pubmed rewrite",
            "pseudo_doc": "irrelevant pseudo abstract with many terms",
        },
    )
    llm = _FakeLLMClient(reply=reply)
    redis_client = _InMemoryRedis()
    settings = get_settings().model_copy(
        update={
            "RANKING_LLM_REWRITE": True,
            "RANKING_QUERY2DOC_ENABLED": False,
        },
    )

    rewrites = await rewrite_for_sources(
        query="x",
        sources=[SourceType.PUBMED],
        llm_client=llm,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        settings=settings,
    )

    assert rewrites[SourceType.PUBMED] == "original pubmed rewrite"
