"""Unit tests for MeSH resolution ranking, fallback, confidence gating, and keyword filtering."""

from __future__ import annotations

from typing import Any

import pytest

from src.workflow import mesh_resolver
from src.workflow.agents.keyword_agent import (
    _collect_other_targets,
    _deduplicate,
    _is_relevant,
)
from src.workflow.state import Suggestion, WorkflowState


def _descriptor_record(
    name: str,
    *,
    uid: str,
    entry_terms: list[str] | None = None,
    tree_numbers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ds_recordtype": "Descriptor",
        "ds_meshui": uid,
        "ds_meshterms": [name, *(entry_terms or [])],
        "ds_subheading": [],
        "ds_idxlinks": [{"treenum": tn} for tn in (tree_numbers or ["C14.280"])],
        "ds_scopenote": "",
    }


# ── MeSH resolver tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_mesh_descriptor_prefers_best_ranked_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver should pick the descriptor with stronger lexical relevance."""

    async def fake_esearch(*_args, **_kwargs) -> tuple[list[str], str | None]:
        return ["u1", "u2"], None

    async def fake_esummary(uid: str, *_args, **_kwargs) -> dict[str, Any] | None:
        if uid == "u1":
            return _descriptor_record("Avian Species", uid=uid)
        return _descriptor_record("Diabetes Mellitus", uid=uid, entry_terms=["Diabetes"])

    monkeypatch.setattr(mesh_resolver, "_esearch_mesh", fake_esearch)
    monkeypatch.setattr(mesh_resolver, "_esummary_mesh", fake_esummary)

    descriptor, translation = await mesh_resolver.resolve_mesh_descriptor(
        term="diabetes",
        client=None,  # type: ignore[arg-type]
        api_key="",
        email="",
    )

    assert translation is None
    assert descriptor is not None
    assert descriptor["name"] == "Diabetes Mellitus"
    assert descriptor["resolution_confidence"] > 0.0


@pytest.mark.asyncio
async def test_resolve_mesh_descriptor_uses_translation_heading_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Translation-heading exact match should outrank weaker nearby candidates."""

    async def fake_esearch(*_args, **_kwargs) -> tuple[list[str], str | None]:
        return ["u1", "u2"], "Heart Failure"

    async def fake_esummary(uid: str, *_args, **_kwargs) -> dict[str, Any] | None:
        if uid == "u1":
            return _descriptor_record("Cardiac Output", uid=uid)
        return _descriptor_record("Heart Failure", uid=uid, entry_terms=["CHF"])

    monkeypatch.setattr(mesh_resolver, "_esearch_mesh", fake_esearch)
    monkeypatch.setattr(mesh_resolver, "_esummary_mesh", fake_esummary)

    descriptor, translation = await mesh_resolver.resolve_mesh_descriptor(
        term="heart failure",
        client=None,  # type: ignore[arg-type]
        api_key="",
        email="",
    )

    assert translation == "Heart Failure"
    assert descriptor is not None
    assert descriptor["name"] == "Heart Failure"


@pytest.mark.asyncio
async def test_resolve_mesh_descriptor_rejects_low_confidence_unrelated_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrelated candidates should be dropped by confidence gating."""

    async def fake_esearch(*_args, **_kwargs) -> tuple[list[str], str | None]:
        return ["u1"], None

    async def fake_esummary(_uid: str, *_args, **_kwargs) -> dict[str, Any] | None:
        return _descriptor_record("Avian Species", uid="u1")

    monkeypatch.setattr(mesh_resolver, "_esearch_mesh", fake_esearch)
    monkeypatch.setattr(mesh_resolver, "_esummary_mesh", fake_esummary)

    descriptor, _translation = await mesh_resolver.resolve_mesh_descriptor(
        term="aspirin",
        client=None,  # type: ignore[arg-type]
        api_key="",
        email="",
    )

    assert descriptor is None


@pytest.mark.asyncio
async def test_resolve_mesh_descriptor_uses_broader_fallback_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver should still recover from strict-query misses via broad fallback."""

    async def fake_esearch(term: str, *_args, **_kwargs) -> tuple[list[str], str | None]:
        if "[MeSH Terms]" in term or term.startswith('"'):
            return [], None
        return ["u1"], "Hypertension"

    async def fake_esummary(_uid: str, *_args, **_kwargs) -> dict[str, Any] | None:
        return _descriptor_record("Hypertension", uid="u1")

    monkeypatch.setattr(mesh_resolver, "_esearch_mesh", fake_esearch)
    monkeypatch.setattr(mesh_resolver, "_esummary_mesh", fake_esummary)

    descriptor, translation = await mesh_resolver.resolve_mesh_descriptor(
        term="hypertension",
        client=None,  # type: ignore[arg-type]
        api_key="",
        email="",
    )

    assert translation == "Hypertension"
    assert descriptor is not None
    assert descriptor["name"] == "Hypertension"


@pytest.mark.asyncio
async def test_resolve_rejects_overly_specific_descriptor_via_length_penalty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A descriptor name much longer than the input should be penalized."""

    async def fake_esearch(*_args, **_kwargs) -> tuple[list[str], str | None]:
        return ["u1"], None

    async def fake_esummary(_uid: str, *_args, **_kwargs) -> dict[str, Any] | None:
        return _descriptor_record(
            "Receptors, Tumor Necrosis Factor, Type II",
            uid="u1",
            tree_numbers=["D12.776.543.750.705.852.420"],
        )

    monkeypatch.setattr(mesh_resolver, "_esearch_mesh", fake_esearch)
    monkeypatch.setattr(mesh_resolver, "_esummary_mesh", fake_esummary)

    descriptor, _ = await mesh_resolver.resolve_mesh_descriptor(
        term="TNF",
        client=None,  # type: ignore[arg-type]
        api_key="",
        email="",
    )

    assert descriptor is None


def test_descriptor_to_mesh_suggestion_includes_resolution_confidence() -> None:
    """Mapped MeshDescriptor should expose resolver confidence when present."""
    suggestion = mesh_resolver.descriptor_to_mesh_suggestion(
        {
            "uid": "D006973",
            "name": "Hypertension",
            "entry_terms": ["High Blood Pressure"],
            "subheadings": ["therapy"],
            "tree_numbers": ["C14.907.489"],
            "min_depth": 3,
            "scope_note": "A chronic condition.",
            "resolution_confidence": 0.77,
        },
        concept="P",
        base_term="hypertension",
    )

    assert suggestion.resolution_confidence == 0.77


def test_score_descriptor_applies_name_length_penalty() -> None:
    """Descriptors with vastly more tokens than the input should score lower."""
    short_desc = {"name": "Aspirin", "entry_terms": [], "min_depth": 2}
    long_desc = {
        "name": "Aspirin Related Gastrointestinal Hemorrhage Syndrome",
        "entry_terms": [],
        "min_depth": 2,
    }

    short_score = mesh_resolver._score_descriptor(
        short_desc, input_term="aspirin", translation_term=None,
    )
    long_score = mesh_resolver._score_descriptor(
        long_desc, input_term="aspirin", translation_term=None,
    )

    assert short_score > long_score


# ── Keyword agent filtering tests ───────────────────────────────


def test_is_relevant_rejects_terms_exceeding_max_length() -> None:
    long_term = "x" * 81
    assert _is_relevant(long_term, "metformin", 0.9, set()) is False


def test_is_relevant_rejects_cross_concept_bleed() -> None:
    assert _is_relevant("metformin", "diabetes", 0.9, {"metformin"}) is False


def test_is_relevant_rejects_low_confidence() -> None:
    assert _is_relevant("totally unrelated", "diabetes", 0.2, set()) is False


def test_is_relevant_rejects_no_overlap_and_no_confidence() -> None:
    assert _is_relevant("ocean waves", "diabetes", None, set()) is False


def test_is_relevant_accepts_overlapping_term() -> None:
    assert _is_relevant("type 2 diabetes mellitus", "type 2 diabetes", 0.8, set()) is True


def test_is_relevant_accepts_high_confidence_even_without_overlap() -> None:
    """Abbreviations like 'DM' share no tokens with 'diabetes' but are valid."""
    assert _is_relevant("DM", "diabetes mellitus", 0.7, set()) is True


def test_deduplicate_keeps_first_occurrence() -> None:
    suggestions = [
        Suggestion(term="Hypertension", concept="P", base_term="htn"),
        Suggestion(term="hypertension", concept="P", base_term="htn"),
        Suggestion(term="High BP", concept="P", base_term="htn"),
    ]
    result = _deduplicate(suggestions)
    assert len(result) == 2
    assert result[0].term == "Hypertension"
    assert result[1].term == "High BP"


def test_collect_other_targets_excludes_current_concept() -> None:
    state = WorkflowState(
        session_id="test",
        question="test",
        atomic_targets={
            "P": ["diabetes"],
            "I": ["metformin"],
            "C": ["placebo"],
            "O": ["mortality"],
        },
    )
    others = _collect_other_targets(state, "I")
    assert "metformin" not in others
    assert "diabetes" in others
    assert "placebo" in others
    assert "mortality" in others
