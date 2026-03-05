"""Version 1 API routers."""

from src.api.v1.chat import router as chat_router
from src.api.v1.enrichment import router as enrichment_router
from src.api.v1.prisma import router as prisma_router
from src.api.v1.search import router as search_router

__all__ = ["chat_router", "search_router", "enrichment_router", "prisma_router"]
