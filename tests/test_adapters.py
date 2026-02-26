"""Tests for Agent A query syntax adapters."""

from __future__ import annotations

import pytest

from src.ai.adapters import translate_for_all_sources
from src.ai.adapters.clinicaltrials_adapter import ClinicalTrialsAdapter
from src.ai.adapters.europepmc_adapter import EuropePMCAdapter
from src.ai.adapters.openalex_adapter import OpenAlexAdapter
from src.ai.adapters.pubmed_adapter import PubMedAdapter
from src.schemas.enums import QueryType, SourceType
from src.schemas.pico import PICOInput


@pytest.mark.asyncio
async def test_pubmed_adapter_translates_free_text_to_boolean_with_field_tags() -> None:
    """Free-text queries should become tagged PubMed Boolean blocks."""
    adapter = PubMedAdapter()

    translated = await adapter.translate(
        query="Does metformin reduce cardiovascular risk",
        query_type=QueryType.FREE,
    )

    assert translated
    assert "[tiab]" in translated
    assert "[MeSH]" in translated
    assert "AND" in translated
    assert "metformin" in translated.lower()


@pytest.mark.asyncio
async def test_pubmed_adapter_passes_through_boolean_query() -> None:
    """Boolean syntax should be forwarded unchanged for PubMed."""
    adapter = PubMedAdapter()
    raw_boolean = '(metformin[tiab] OR "metformin"[MeSH]) AND (risk[tiab])'

    translated = await adapter.translate(query=raw_boolean, query_type=QueryType.BOOLEAN)

    assert translated == raw_boolean


@pytest.mark.asyncio
async def test_pubmed_adapter_builds_boolean_from_pico_components() -> None:
    """PICO input should be composed into AND-connected field blocks."""
    adapter = PubMedAdapter()
    pico = PICOInput(
        population="adults with diabetes",
        intervention="metformin",
        comparison="placebo",
        outcome="cardiovascular risk",
    )

    translated = await adapter.translate(query="ignored", query_type=QueryType.PICO, pico=pico)

    assert "adults with diabetes" in translated
    assert "metformin" in translated
    assert "placebo" in translated
    assert "cardiovascular risk" in translated
    assert translated.count(" AND ") >= 3
    assert "[tiab]" in translated


@pytest.mark.asyncio
async def test_openalex_adapter_strips_pubmed_field_tags() -> None:
    """OpenAlex translations should remove PubMed field tags."""
    adapter = OpenAlexAdapter()
    query = '(metformin[tiab] OR "type 2 diabetes"[MeSH]) AND (cardiovascular[pt])'

    translated = await adapter.translate(query=query, query_type=QueryType.BOOLEAN)

    assert "[tiab]" not in translated
    assert "[MeSH]" not in translated
    assert "[pt]" not in translated
    assert "type 2 diabetes" in translated
    assert "AND" in translated
    assert "OR" in translated


@pytest.mark.asyncio
async def test_clinicaltrials_adapter_simplifies_complex_boolean_to_keywords() -> None:
    """ClinicalTrials adapter should flatten and simplify complex queries."""
    adapter = ClinicalTrialsAdapter()
    query = (
        '((metformin[tiab] OR "type 2 diabetes"[MeSH]) '
        "AND (cardiovascular[tiab] OR mortality[tiab])) NOT placebo[pt]"
    )

    translated = await adapter.translate(query=query, query_type=QueryType.BOOLEAN)
    terms = [term for term in translated.split(" AND ") if term.strip()]

    assert translated
    assert "[" not in translated
    assert "]" not in translated
    assert "(" not in translated
    assert ")" not in translated
    assert 3 <= len(terms) <= 5


@pytest.mark.asyncio
async def test_translate_for_all_sources_returns_requested_sources_mapping() -> None:
    """Batch translation should produce one query per requested source."""
    requested = [
        SourceType.PUBMED,
        SourceType.EUROPEPMC,
        SourceType.OPENALEX,
        SourceType.CLINICALTRIALS,
    ]

    translated = await translate_for_all_sources(
        query="Does metformin reduce cardiovascular risk",
        query_type=QueryType.FREE,
        pico=None,
        sources=requested,
    )

    assert set(translated.keys()) == set(requested)
    assert all(isinstance(value, str) for value in translated.values())
    assert all(value for value in translated.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter", "expects_pubmed_field_tags"),
    [
        (PubMedAdapter(), True),
        (EuropePMCAdapter(), True),
        (OpenAlexAdapter(), False),
        (ClinicalTrialsAdapter(), False),
    ],
)
async def test_abstract_query_type_extracts_keywords_for_each_adapter(
    adapter: PubMedAdapter | EuropePMCAdapter | OpenAlexAdapter | ClinicalTrialsAdapter,
    expects_pubmed_field_tags: bool,
) -> None:
    """All adapters should produce non-empty abstract-derived query terms."""
    abstract_text = (
        "Adults with type 2 diabetes received metformin therapy, which reduced "
        "cardiovascular risk and all-cause mortality compared with placebo."
    )

    translated = await adapter.translate(query=abstract_text, query_type=QueryType.ABSTRACT)

    assert translated
    if expects_pubmed_field_tags:
        assert "[tiab]" in translated
    else:
        assert "[tiab]" not in translated
