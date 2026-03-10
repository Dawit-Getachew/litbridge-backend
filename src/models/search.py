"""Search-related ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.user import User


class SearchSession(Base):
    """Persisted search session and serialized result payloads."""

    __tablename__ = "search_sessions"

    user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    query: Mapped[str] = mapped_column(String, nullable=False)
    query_type: Mapped[str] = mapped_column(String(32), nullable=False)
    search_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    pico: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    total_identified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_after_dedup: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    sources_completed: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    sources_failed: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User | None] = relationship("User", back_populates="search_sessions")


Index(
    "ix_search_sessions_created_at_desc",
    SearchSession.created_at.desc(),
)
