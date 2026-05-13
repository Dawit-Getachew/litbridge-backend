"""add litpulse_user_id to users for cross-service auth bridge

Revision ID: f1a2b3c4d5e6
Revises: e5f8a1b34c67
Create Date: 2026-05-06 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e5f8a1b34c67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable, unique-when-set litpulse_user_id column to users."""
    op.add_column(
        "users",
        sa.Column("litpulse_user_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_users_litpulse_user_id",
        "users",
        ["litpulse_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_litpulse_user_id", table_name="users")
    op.drop_column("users", "litpulse_user_id")
