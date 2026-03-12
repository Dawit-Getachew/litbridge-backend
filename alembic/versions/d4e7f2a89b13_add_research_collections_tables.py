"""add research_collections and research_collection_items tables

Revision ID: d4e7f2a89b13
Revises: c9a1d4e67f02
Create Date: 2026-03-12 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d4e7f2a89b13"
down_revision: str | Sequence[str] | None = "c9a1d4e67f02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_collections",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(length=64), nullable=True),
        sa.Column("color", sa.String(length=32), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_collections_user_id",
        "research_collections",
        ["user_id"],
    )

    op.create_table(
        "research_collection_items",
        sa.Column("collection_id", sa.UUID(), nullable=False),
        sa.Column("record_id", sa.String(), nullable=False),
        sa.Column("search_session_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["research_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["search_session_id"],
            ["search_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collection_id", "record_id", name="uq_research_collection_item",
        ),
    )
    op.create_index(
        "ix_research_collection_items_collection_id",
        "research_collection_items",
        ["collection_id"],
    )
    op.create_index(
        "ix_research_collection_items_record_id",
        "research_collection_items",
        ["record_id"],
    )
    op.create_index(
        "ix_research_collection_items_search_session_id",
        "research_collection_items",
        ["search_session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_collection_items_search_session_id",
        table_name="research_collection_items",
    )
    op.drop_index(
        "ix_research_collection_items_record_id",
        table_name="research_collection_items",
    )
    op.drop_index(
        "ix_research_collection_items_collection_id",
        table_name="research_collection_items",
    )
    op.drop_table("research_collection_items")

    op.drop_index(
        "ix_research_collections_user_id",
        table_name="research_collections",
    )
    op.drop_table("research_collections")
