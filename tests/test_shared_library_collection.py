"""Tests for the read-through virtual "LitPulse Library" collection.

This is what makes papers saved in LitPulse appear inside LitPortal's
collections list. The papers live in the central LitHub library (keyed by the
user's Identity sub); we surface them as a single read-only synthetic
collection mapped into the existing CollectionItemResponse shape — no DB rows,
no schema change.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.services.research_collection_service import (
    SHARED_LIBRARY_COLLECTION_ID,
    SHARED_LIBRARY_NAME,
    ResearchCollectionService,
)


class _FakeLitHubClient:
    def __init__(self, payload):
        self._payload = payload
        self.list_calls = 0
        self.last_user_id = None

    async def internal_list_library(self, user_id, *, params=None):
        self.list_calls += 1
        self.last_user_id = user_id
        return self._payload


def _svc(payload, *, enabled=True):
    client = _FakeLitHubClient(payload)
    svc = ResearchCollectionService(repo=None, lithub_client=client, lithub_enabled=enabled)
    return svc, client


async def test_shared_library_maps_lithub_articles_to_items():
    identity = uuid4()
    svc, client = _svc({
        "articles": [
            {
                "paper_id": "11111111-1111-1111-1111-111111111111",
                "pmid": "12345678",
                "title": "Effect of BRCA1 on tumor suppression",
                "journal": "Nature",
                "pub_date": "2024",
                "ai_summary": "BRCA1 loss accelerates tumorigenesis.",
                "study_design": "rct",
                "saved_at": "2026-05-01T10:00:00+00:00",
            },
            {
                "doi": "10.1/abc",
                "title": "A second paper",
                "saved_at": "2026-05-02T10:00:00Z",
            },
        ],
        "total": 2,
    })

    detail = await svc.build_shared_library_detail(identity)

    assert detail is not None
    assert detail.id == SHARED_LIBRARY_COLLECTION_ID
    assert detail.name == SHARED_LIBRARY_NAME
    assert detail.item_count == 2
    assert len(detail.items) == 2
    assert client.last_user_id == identity

    first = detail.items[0]
    assert first.title == "Effect of BRCA1 on tumor suppression"
    assert first.collection_id == SHARED_LIBRARY_COLLECTION_ID
    # metadata backfilled from the LitHub article (PaperMetadata shape preserved)
    assert first.metadata.key_findings == "BRCA1 loss accelerates tumorigenesis."
    assert str(first.metadata.study_design) in ("StudyDesign.rct", "rct") or first.metadata.study_design is not None


async def test_shared_library_item_id_is_deterministic():
    identity = uuid4()
    art = {"paper_id": "22222222-2222-2222-2222-222222222222", "title": "Stable"}
    svc1, _ = _svc({"articles": [art], "total": 1})
    svc2, _ = _svc({"articles": [art], "total": 1})

    d1 = await svc1.build_shared_library_detail(identity)
    d2 = await svc2.build_shared_library_detail(identity)
    assert d1.items[0].id == d2.items[0].id  # same paper -> same synthetic id


async def test_shared_library_none_when_disabled():
    svc, _ = _svc({"articles": [], "total": 0}, enabled=False)
    assert await svc.build_shared_library_detail(uuid4()) is None


async def test_shared_library_none_when_no_identity():
    svc, client = _svc({"articles": [], "total": 0})
    assert await svc.build_shared_library_detail(None) is None
    assert client.list_calls == 0  # never calls LitHub without an identity


async def test_shared_library_none_on_lithub_error():
    class _Boom:
        async def internal_list_library(self, user_id, *, params=None):
            raise RuntimeError("lithub down")

    svc = ResearchCollectionService(repo=None, lithub_client=_Boom(), lithub_enabled=True)
    # Best-effort: a LitHub outage must not break the collections page.
    assert await svc.build_shared_library_detail(uuid4()) is None


async def test_shared_library_empty_title_falls_back():
    svc, _ = _svc({"articles": [{"pmid": "999", "title": ""}], "total": 1})
    detail = await svc.build_shared_library_detail(uuid4())
    assert detail.items[0].title == "Untitled paper"


async def test_shared_library_accepts_string_identity():
    identity_str = "33333333-3333-3333-3333-333333333333"
    svc, client = _svc({"articles": [], "total": 0})
    detail = await svc.build_shared_library_detail(identity_str)
    assert detail is not None
    assert client.last_user_id == UUID(identity_str)
