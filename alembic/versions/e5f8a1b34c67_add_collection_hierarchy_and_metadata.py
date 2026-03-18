"""add parent_id to research_collections and metadata to items

Revision ID: e5f8a1b34c67
Revises: d4e7f2a89b13
Create Date: 2026-03-18 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "e5f8a1b34c67"
down_revision: str | Sequence[str] | None = "d4e7f2a89b13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_collections",
        sa.Column("parent_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_research_collections_parent_id",
        "research_collections",
        "research_collections",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_research_collections_parent_id",
        "research_collections",
        ["parent_id"],
    )

    op.add_column(
        "research_collection_items",
        sa.Column("metadata_extracted", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_collection_items", "metadata_extracted")

    op.drop_index("ix_research_collections_parent_id", table_name="research_collections")
    op.drop_constraint(
        "fk_research_collections_parent_id",
        "research_collections",
        type_="foreignkey",
    )
    op.drop_column("research_collections", "parent_id")
