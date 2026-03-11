"""Library and LibraryItem ORM models for organizing searches into collections."""

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


class Library(Base):
    """A user-owned collection/folder for organizing searches."""

    __tablename__ = "libraries"

    user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("libraries.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped[User] = relationship("User", back_populates="libraries")
    parent: Mapped[Library | None] = relationship(
        "Library", remote_side="Library.id", back_populates="children",
    )
    children: Mapped[list[Library]] = relationship(
        "Library", back_populates="parent", cascade="all, delete-orphan",
        lazy="selectin",
    )
    items: Mapped[list[LibraryItem]] = relationship(
        back_populates="library", cascade="all, delete-orphan", lazy="selectin",
    )


class LibraryItem(Base):
    """Association between a library and a search session."""

    __tablename__ = "library_items"
    __table_args__ = (
        UniqueConstraint("library_id", "search_session_id", name="uq_library_item"),
    )

    library_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    search_session_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    library: Mapped[Library] = relationship("Library", back_populates="items")
    search_session: Mapped[SearchSession] = relationship("SearchSession", lazy="joined")
