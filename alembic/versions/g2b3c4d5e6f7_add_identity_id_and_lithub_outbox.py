"""add identity_id on users and lithub_sync_outbox

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-01 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "g2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── users.identity_id ─────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("identity_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_users_identity_id",
        "users",
        ["identity_id"],
        unique=True,
        postgresql_where=sa.text("identity_id IS NOT NULL"),
    )

    # ── lithub_sync_outbox ────────────────────────────────────────
    op.create_table(
        "lithub_sync_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="'pending'"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_status_attempt",
        "lithub_sync_outbox",
        ["status", "next_attempt_at"],
    )
    op.create_index("ix_outbox_user", "lithub_sync_outbox", ["user_id"])

    # ── research_collection_items.paper_id ────────────────────────
    # The canonical LitHub paper UUID for cross-service enrichment + linkage.
    op.add_column(
        "research_collection_items",
        sa.Column("paper_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_research_collection_items_paper_id",
        "research_collection_items",
        ["paper_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_collection_items_paper_id",
        table_name="research_collection_items",
    )
    op.drop_column("research_collection_items", "paper_id")
    op.drop_index("ix_outbox_user", table_name="lithub_sync_outbox")
    op.drop_index("ix_outbox_status_attempt", table_name="lithub_sync_outbox")
    op.drop_table("lithub_sync_outbox")
    op.drop_index("ix_users_identity_id", table_name="users")
    op.drop_column("users", "identity_id")
