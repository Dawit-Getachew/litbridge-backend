"""add user_id to search_sessions and create libraries tables

Revision ID: c9a1d4e67f02
Revises: b7c4e1f23a89
Create Date: 2026-03-10 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c9a1d4e67f02"
down_revision: str | Sequence[str] | None = "b7c4e1f23a89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Add user_id to search_sessions --
    op.add_column(
        "search_sessions",
        sa.Column("user_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_search_sessions_user_id",
        "search_sessions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_search_sessions_user_id",
        "search_sessions",
        ["user_id"],
    )

    # -- Create libraries table --
    op.create_table(
        "libraries",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["parent_id"], ["libraries.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_libraries_user_id", "libraries", ["user_id"])
    op.create_index("ix_libraries_parent_id", "libraries", ["parent_id"])

    # -- Create library_items join table --
    op.create_table(
        "library_items",
        sa.Column("library_id", sa.UUID(), nullable=False),
        sa.Column("search_session_id", sa.UUID(), nullable=False),
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
            ["library_id"], ["libraries.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["search_session_id"], ["search_sessions.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "library_id", "search_session_id", name="uq_library_item",
        ),
    )
    op.create_index(
        "ix_library_items_library_id", "library_items", ["library_id"],
    )
    op.create_index(
        "ix_library_items_search_session_id",
        "library_items",
        ["search_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_library_items_search_session_id", table_name="library_items")
    op.drop_index("ix_library_items_library_id", table_name="library_items")
    op.drop_table("library_items")

    op.drop_index("ix_libraries_parent_id", table_name="libraries")
    op.drop_index("ix_libraries_user_id", table_name="libraries")
    op.drop_table("libraries")

    op.drop_index("ix_search_sessions_user_id", table_name="search_sessions")
    op.drop_constraint(
        "fk_search_sessions_user_id", "search_sessions", type_="foreignkey",
    )
    op.drop_column("search_sessions", "user_id")
