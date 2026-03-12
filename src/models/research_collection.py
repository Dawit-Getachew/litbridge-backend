"""ResearchCollection and ResearchCollectionItem ORM models for organizing individual records."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID as PyUUID

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.search import SearchSession
    from src.models.user import User


class ResearchCollection(Base):
    """A user-owned flat collection for organizing individual records (papers)."""

    __tablename__ = "research_collections"

    user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped[User] = relationship("User", back_populates="research_collections")
    items: Mapped[list[ResearchCollectionItem]] = relationship(
        back_populates="collection", cascade="all, delete-orphan", lazy="selectin",
    )


class ResearchCollectionItem(Base):
    """Association between a research collection and an individual record."""

    __tablename__ = "research_collection_items"
    __table_args__ = (
        UniqueConstraint("collection_id", "record_id", name="uq_research_collection_item"),
    )

    collection_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    record_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    search_session_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    collection: Mapped[ResearchCollection] = relationship(
        "ResearchCollection", back_populates="items",
    )
    search_session: Mapped[SearchSession] = relationship("SearchSession", lazy="joined")
