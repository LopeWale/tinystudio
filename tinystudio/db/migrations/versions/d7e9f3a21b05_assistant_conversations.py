"""Persist private assistant conversations with optimistic revisions.

Revision ID: d7e9f3a21b05
Revises: c5d8e2f71a04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d7e9f3a21b05"
down_revision = "c5d8e2f71a04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_conversations",
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("history", postgresql.JSONB(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(300), nullable=False),
        sa.Column("updated", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("revision > 0", name="ck_assistant_conversation_revision"),
    )
    op.create_index("ix_assistant_conversations_user", "assistant_conversations", ["user_id"])


def downgrade() -> None:
    op.drop_table("assistant_conversations")
