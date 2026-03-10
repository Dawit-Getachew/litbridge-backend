"""MeSH descriptor resolver via NCBI E-utilities (ESearch + ESummary)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from src.workflow.state import MeshDescriptor, MeshQualifiers

logger = structlog.get_logger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

_rate_semaphore = asyncio.Semaphore(10)
_MIN_REQUEST_INTERVAL = 0.11  # ~9 req/s with API key


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


async def _eutils_get(
    client: httpx.AsyncClient,
    url: str,
) -> dict[str, Any]:
    """Rate-limited GET returning parsed JSON."""
    async with _rate_semaphore:
        await asyncio.sleep(_MIN_REQUEST_INTERVAL)
        resp = await client.get(url, timeout=30.0)
        resp.raise_for_status()
        return resp.json()


async def _esearch_mesh(
    term: str,
    client: httpx.AsyncClient,
    api_key: str,
    email: str,
    retmax: int = 5,
) -> tuple[list[str], str | None]:
    """Search the MeSH database. Returns (uid_list, translation_heading)."""
    params: dict[str, str] = {
        "db": "mesh",
        "retmode": "json",
        "retmax": str(retmax),
        "term": term,
    }
    if api_key:
        params["api_key"] = api_key
    if email:
        params["email"] = email
        params["tool"] = "litbridge"

    url = f"{EUTILS_BASE}/esearch.fcgi?{urlencode(params)}"
    data = await _eutils_get(client, url)

    esr = data.get("esearchresult", {}) or {}
    idlist = list(esr.get("idlist", []) or [])

    heading: str | None = None
    try:
        for tr in esr.get("translationset", []) or []:
            to_str = str(tr.get("to", ""))
            if "[MeSH Terms]" in to_str:
                i1 = to_str.find('"')
                i2 = to_str.find('"', i1 + 1) if i1 >= 0 else -1
                if 0 <= i1 < i2:
                    heading = to_str[i1 + 1 : i2]
                    break
    except Exception:
        pass

    return idlist, heading


async def _esummary_mesh(
    uid: str,
    client: httpx.AsyncClient,
    api_key: str,
    email: str,
) -> dict[str, Any] | None:
    """Fetch one MeSH summary record by UID."""
    params: dict[str, str] = {"db": "mesh", "retmode": "json", "id": uid}
    if api_key:
        params["api_key"] = api_key
    if email:
        params["email"] = email
        params["tool"] = "litbridge"

    url = f"{EUTILS_BASE}/esummary.fcgi?{urlencode(params)}"
    data = await _eutils_get(client, url)

    result = data.get("result", {}) or {}
    uids = result.get("uids", []) or []
    if not uids:
        return None
    return result.get(str(uids[0]))


def _parse_descriptor(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Parse descriptor fields from an ESummary record. Returns None if not a descriptor."""
    if str(rec.get("ds_recordtype", "")).strip().lower() != "descriptor":
        return None

    meshterms = [str(x).strip() for x in (rec.get("ds_meshterms") or []) if str(x).strip()]
    if not meshterms:
        return None

    tree_numbers: list[str] = []
    for link in rec.get("ds_idxlinks", []) or []:
        tn = str(link.get("treenum", "")).strip()
        if tn:
            tree_numbers.append(tn)

    min_depth: int | None = None
    if tree_numbers:
        depths = [len(t.split(".")) for t in tree_numbers if t]
        min_depth = min(depths) if depths else None

    return {
        "uid": str(rec.get("ds_meshui", "")).strip(),
        "name": meshterms[0],
        "entry_terms": meshterms[1:],
        "subheadings": [str(x).strip() for x in (rec.get("ds_subheading") or []) if str(x).strip()],
        "tree_numbers": tree_numbers,
        "min_depth": min_depth,
        "scope_note": str(rec.get("ds_scopenote", "")).strip() or None,
    }


async def resolve_mesh_descriptor(
    term: str,
    client: httpx.AsyncClient,
    api_key: str,
    email: str,
    max_uids: int = 5,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve a term to a MeSH descriptor dict.

    Returns (descriptor_dict, translation_heading). descriptor_dict is None
    if no canonical descriptor was found.
    """
    idlist, translation = await _esearch_mesh(
        term, client, api_key, email, retmax=max_uids,
    )
    if not idlist:
        return None, translation

    translation_norm = _norm(translation) if translation else None
    chosen: dict[str, Any] | None = None

    for uid in idlist[:max_uids]:
        rec = await _esummary_mesh(uid, client, api_key, email)
        if not rec:
            continue
        desc = _parse_descriptor(rec)
        if not desc:
            continue

        if translation_norm and _norm(str(desc.get("name", ""))) == translation_norm:
            return desc, translation

        if chosen is None:
            chosen = desc

    return chosen, translation


def descriptor_to_mesh_suggestion(
    descriptor: dict[str, Any],
    *,
    concept: str,
    base_term: str,
    status: str = "suggested",
) -> MeshDescriptor:
    """Convert a resolved descriptor dict into a MeshDescriptor model."""
    name = str(descriptor.get("name", "")).strip()
    uid = str(descriptor.get("uid", "")).strip() or None
    entry_terms = list(descriptor.get("entry_terms", []))
    subheadings = list(descriptor.get("subheadings", []))
    tree_numbers = list(descriptor.get("tree_numbers", []))
    min_depth = descriptor.get("min_depth")
    scope_note = descriptor.get("scope_note")

    selected_entries = entry_terms[:5]

    return MeshDescriptor(
        mesh_term=name,
        concept=concept,
        base_term=base_term,
        status=status,
        descriptor_uid=uid,
        descriptor_name=name,
        entry_terms=entry_terms,
        entry_terms_selected=selected_entries,
        qualifiers=MeshQualifiers(allowed=subheadings, selected=[]),
        tree_numbers=tree_numbers,
        min_depth=min_depth if isinstance(min_depth, int) else None,
        explode=True,
        scope_note=scope_note[:300] if scope_note else None,
    )
