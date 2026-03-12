"""User and refresh-token ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID as PyUUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.library import Library
    from src.models.research_collection import ResearchCollection
    from src.models.search import SearchSession


class User(Base):
    """Application user, provider-agnostic."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default="'email'")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin",
    )
    search_sessions: Mapped[list[SearchSession]] = relationship(
        back_populates="user", lazy="noload",
    )
    libraries: Mapped[list[Library]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="noload",
    )
    research_collections: Mapped[list[ResearchCollection]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="noload",
    )


class RefreshToken(Base):
    """Opaque refresh token, stored as a SHA-256 hash."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_token_hash", "token_hash"),
        Index("ix_refresh_tokens_user_id", "user_id"),
    )

    user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    device_info: Mapped[str | None] = mapped_column(String(512), nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")
