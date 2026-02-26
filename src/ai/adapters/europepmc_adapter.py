"""Europe PMC query translation adapter."""

from __future__ import annotations

from src.ai.adapters.pubmed_adapter import PubMedAdapter
from src.schemas.enums import SourceType


class EuropePMCAdapter(PubMedAdapter):
    """Europe PMC accepts nearly identical syntax to PubMed."""

    source = SourceType.EUROPEPMC
