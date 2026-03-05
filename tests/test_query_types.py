"""Tests for query-type handling across translation adapters and request validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.ai.adapters import translate_for_all_sources
from src.schemas.enums import QueryType, SourceType
from src.schemas.pico import PICOInput
from src.schemas.search import SearchRequest


@pytest.mark.asyncio
async def test_free_query_translates_for_all_selected_sources() -> None:
    translations = await translate_for_all_sources(
        query="metformin cardiovascular mortality adults",
        query_type=QueryType.FREE,
        sources=list(SourceType),
    )

    assert set(translations.keys()) == set(SourceType)
    assert all(isinstance(value, str) and value.strip() for value in translations.values())


@pytest.mark.asyncio
async def test_pico_query_generates_boolean_style_translation() -> None:
    translations = await translate_for_all_sources(
        query="heart failure metformin placebo mortality",
        query_type=QueryType.PICO,
        pico=PICOInput(
            population="adults with heart failure",
            intervention="metformin",
            comparison="placebo",
            outcome="cardiovascular mortality",
        ),
        sources=[SourceType.PUBMED],
    )

    pubmed_query = translations[SourceType.PUBMED]
    assert "AND" in pubmed_query
    assert "[tiab]" in pubmed_query


@pytest.mark.asyncio
async def test_boolean_query_passes_through_for_pubmed() -> None:
    boolean_query = '(metformin[tiab] AND "heart failure"[tiab]) NOT pediatric[tiab]'
    translations = await translate_for_all_sources(
        query=boolean_query,
        query_type=QueryType.BOOLEAN,
        sources=[SourceType.PUBMED],
    )

    assert translations[SourceType.PUBMED] == boolean_query


@pytest.mark.asyncio
async def test_abstract_query_extracts_keywords() -> None:
    abstract = (
        "Background: Metformin has been proposed to improve cardiovascular outcomes in adults "
        "with type 2 diabetes and heart failure. Methods: We evaluated randomized trials and "
        "observational evidence with mortality and hospitalization outcomes over 24 months."
    )
    translations = await translate_for_all_sources(
        query=abstract,
        query_type=QueryType.ABSTRACT,
        sources=[SourceType.OPENALEX, SourceType.CLINICALTRIALS],
    )

    assert translations[SourceType.OPENALEX]
    assert translations[SourceType.CLINICALTRIALS]
    assert translations[SourceType.OPENALEX] != abstract
    assert translations[SourceType.CLINICALTRIALS] != abstract


def test_pico_query_requires_at_least_one_non_empty_component() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(
            query="heart failure",
            query_type=QueryType.PICO,
            pico=PICOInput(
                population=" ",
                intervention="",
                comparison=None,
                outcome="",
            ),
        )


def test_abstract_query_requires_minimum_length() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(
            query="too short abstract",
            query_type=QueryType.ABSTRACT,
        )
