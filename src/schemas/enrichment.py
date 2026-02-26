"""Schema for per-record enrichment payloads."""

from pydantic import BaseModel

from src.schemas.enums import OAStatus


class EnrichmentResponse(BaseModel):
    """Enrichment details for an individual record."""

    id: str
    tldr: str | None = None
    citation_count: int | None = None
    oa_status: OAStatus | None = None
    pdf_url: str | None = None
