"""Unit tests for MeSH resolution ranking, fallback, and confidence gating."""

from __future__ import annotations

from typing import Any

import pytest

from src.workflow import mesh_resolver


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
