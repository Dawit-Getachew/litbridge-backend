"""Reliable cross-service sync outbox for LitHub library writes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


_PAYLOAD_TYPE = JSONB().with_variant(JSON(), "sqlite")


class LitHubSyncOutbox(Base):
    """Best-effort outbox row capturing a LitHub save that needs retrying.

    The LitPortal BFF appends a row here whenever a local collection write
    succeeded but the corresponding LitHub save failed. A background sweeper
    retries with exponential backoff; rows that succeed are marked
    ``status='sent'`` and pruned after 7 days, rows that hit the max-attempts
    ceiling are marked ``status='dead'`` and surfaced via an admin endpoint
    for manual inspection.
    """

    __tablename__ = "lithub_sync_outbox"
    __table_args__ = (
        Index("ix_outbox_status_attempt", "status", "next_attempt_at"),
        Index("ix_outbox_user", "user_id"),
    )

    user_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payload: Mapped[dict] = mapped_column(_PAYLOAD_TYPE, nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="'pending'",
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
