"""Domain exception types used across LitBridge layers."""


class LitBridgeError(Exception):
    """Base exception for LitBridge domain errors."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class SourceFetchError(LitBridgeError):
    """Raised when a federated source request fails."""

    def __init__(self, source: str, status_code: int, message: str | None = None) -> None:
        self.source = source
        self.status_code = status_code
        detail = message or f"Failed to fetch from source '{source}'"
        super().__init__(detail)


class DeduplicationError(LitBridgeError):
    """Raised when deduplication logic fails."""


class EnrichmentError(LitBridgeError):
    """Raised when semantic enrichment fails."""


class SearchNotFoundError(LitBridgeError):
    """Raised when the requested search identifier does not exist."""

    def __init__(self, search_id: str, message: str | None = None) -> None:
        self.search_id = search_id
        detail = message or f"Search '{search_id}' was not found"
        super().__init__(detail)


class RateLimitError(LitBridgeError):
    """Raised when an external source signals a rate-limit response."""

    def __init__(self, source: str, retry_after: float, message: str | None = None) -> None:
        self.source = source
        self.retry_after = retry_after
        detail = message or f"Rate limited by '{source}'"
        super().__init__(detail)
