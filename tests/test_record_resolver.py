"""Tests for the natural language record resolver."""

from __future__ import annotations

import pytest

from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import UnifiedRecord
from src.services.record_resolver import resolve_references


def _rec(
    record_id: str,
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
) -> UnifiedRecord:
    return UnifiedRecord(
        id=record_id,
        title=title,
        authors=authors or ["Author One"],
        source=SourceType.PUBMED,
        year=year,
    )


SAMPLE_RECORDS = [
    _rec("r1", "Effect of Hydroxychloroquine in Hospitalized Patients (RECOVERY trial)", ["Horby P"], 2021),
    _rec("r2", "Remdesivir for Treatment of COVID-19", ["Zhang L", "Wang J"], 2024),
    _rec("r3", "WHO Solidarity Trial: repurposed antiviral drugs", ["Pan H"], 2021),
    _rec("r4", "Dexamethasone in Hospitalized Patients with Covid-19", ["Horby P"], 2020),
    _rec("r5", "Ivermectin for Prevention of COVID-19", ["Lopez-Medina E"], 2021),
]


class TestPositionalResolution:
    def test_first_paper(self) -> None:
        result = resolve_references("explain the first paper", SAMPLE_RECORDS)
        assert len(result) == 1
        assert result[0].id == "r1"

    def test_third_result(self) -> None:
        result = resolve_references("tell me about the third result", SAMPLE_RECORDS)
        assert len(result) == 1
        assert result[0].id == "r3"

    def test_last_paper(self) -> None:
        result = resolve_references("summarize the last paper", SAMPLE_RECORDS)
        assert len(result) == 1
        assert result[0].id == "r5"

    def test_paper_number(self) -> None:
        result = resolve_references("what does paper #2 say?", SAMPLE_RECORDS)
        assert len(result) == 1
        assert result[0].id == "r2"

    def test_out_of_range_returns_empty(self) -> None:
        result = resolve_references("explain paper #99", SAMPLE_RECORDS)
        assert result == []


class TestAuthorYearResolution:
    def test_author_year_match(self) -> None:
        result = resolve_references("What does Zhang 2024 conclude?", SAMPLE_RECORDS)
        assert len(result) >= 1
        assert result[0].id == "r2"

    def test_horby_2020(self) -> None:
        result = resolve_references("deep dive into Horby et al. 2020", SAMPLE_RECORDS)
        assert any(r.id == "r4" for r in result)

    def test_no_match_wrong_year(self) -> None:
        result = resolve_references("Zhang 2019 paper", SAMPLE_RECORDS)
        assert not any(r.id == "r2" for r in result)


class TestFuzzyTitleResolution:
    def test_recovery_trial(self) -> None:
        result = resolve_references("explain the RECOVERY trial", SAMPLE_RECORDS)
        assert any(r.id == "r1" for r in result)

    def test_solidarity_trial(self) -> None:
        result = resolve_references("what about the WHO solidarity trial?", SAMPLE_RECORDS)
        assert any(r.id == "r3" for r in result)


class TestKeywordResolution:
    def test_remdesivir_keyword(self) -> None:
        result = resolve_references("that remdesivir study", SAMPLE_RECORDS)
        assert any(r.id == "r2" for r in result)

    def test_ivermectin_keyword(self) -> None:
        result = resolve_references("the ivermectin one", SAMPLE_RECORDS)
        assert any(r.id == "r5" for r in result)


class TestEdgeCases:
    def test_empty_message(self) -> None:
        assert resolve_references("", SAMPLE_RECORDS) == []

    def test_empty_records(self) -> None:
        assert resolve_references("explain something", []) == []

    def test_vague_message_no_matches(self) -> None:
        result = resolve_references("what is the meaning of life", SAMPLE_RECORDS)
        assert isinstance(result, list)

    def test_multiple_references_in_one_message(self) -> None:
        result = resolve_references(
            "compare the first paper with Zhang 2024",
            SAMPLE_RECORDS,
        )
        ids = {r.id for r in result}
        assert "r1" in ids
        assert "r2" in ids
