"""Repository layer package for external data access."""

from __future__ import annotations

import httpx

from src.core.config import get_settings
from src.repositories.base_repo import BaseSourceRepository
from src.repositories.clinicaltrials_repo import ClinicalTrialsRepository
from src.repositories.europepmc_repo import EuropePMCRepository
from src.repositories.openalex_repo import OpenAlexRepository
from src.repositories.pubmed_repo import PubMedRepository
from src.schemas.enums import SourceType


def get_repository(source: SourceType, client: httpx.AsyncClient) -> BaseSourceRepository:
    """Return the repository implementation for a given source."""
    settings = get_settings()

    if source is SourceType.PUBMED:
        return PubMedRepository(client=client, settings=settings)
    if source is SourceType.OPENALEX:
        return OpenAlexRepository(client=client, settings=settings)
    if source is SourceType.EUROPEPMC:
        return EuropePMCRepository(client=client, settings=settings)
    if source is SourceType.CLINICALTRIALS:
        return ClinicalTrialsRepository(client=client, settings=settings)
    raise ValueError(f"Unsupported source: {source!r}")


__all__ = [
    "BaseSourceRepository",
    "ClinicalTrialsRepository",
    "EuropePMCRepository",
    "OpenAlexRepository",
    "PubMedRepository",
    "get_repository",
]
