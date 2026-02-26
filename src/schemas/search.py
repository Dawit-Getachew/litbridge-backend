"""Search request and status schemas for API endpoints."""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from src.schemas.enums import QueryType, SearchMode, SourceType
from src.schemas.pico import PICOInput


class SearchRequest(BaseModel):
    """Search request contract accepted by the API."""

    query: str
    query_type: QueryType = QueryType.FREE
    search_mode: SearchMode = SearchMode.QUICK
    sources: list[SourceType] | None = None
    pico: PICOInput | None = None
    max_results: int = Field(default=100, ge=1, le=5000)

    @model_validator(mode="after")
    def validate_and_normalize(self) -> Self:
        """Validate cross-field requirements and normalize defaults."""

        if self.query_type is QueryType.PICO and self.pico is None:
            raise ValueError("pico must be provided when query_type is structured")
        if self.sources == []:
            raise ValueError("sources cannot be an empty list")
        if self.sources is None:
            self.sources = list(SourceType)
        return self


class SearchResponse(BaseModel):
    """Response returned when a search is started."""

    search_id: str


class SearchStatusResponse(BaseModel):
    """Progress/status response while a search job is running."""

    search_id: str
    status: Literal["processing", "completed", "failed"]
    total_count: int = 0
    sources_completed: list[SourceType] = Field(default_factory=list)
    sources_failed: list[SourceType] = Field(default_factory=list)
    progress_pct: int = Field(default=0, ge=0, le=100)
