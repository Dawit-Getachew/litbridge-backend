"""add conversations and messages tables

Revision ID: a3f8c9d12e45
Revises: 6d221b598335
Create Date: 2026-03-05 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a3f8c9d12e45"
down_revision: str | Sequence[str] | None = "6d221b598335"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create conversations and messages tables."""
    op.create_table(
        "conversations",
        sa.Column("search_session_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["search_session_id"], ["search_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_search_session_id", "conversations", ["search_session_id"])
    op.create_index(
        "ix_conversations_search_session_id_created",
        "conversations",
        ["search_session_id", sa.literal_column("created_at DESC")],
    )

    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("record_ids", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index(
        "ix_messages_conversation_id_created",
        "messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    """Drop messages and conversations tables."""
    op.drop_index("ix_messages_conversation_id_created", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_conversations_search_session_id_created", table_name="conversations")
    op.drop_index("ix_conversations_search_session_id", table_name="conversations")
    op.drop_table("conversations")
