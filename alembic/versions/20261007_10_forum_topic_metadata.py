"""store Telegram forum topic presentation metadata

Revision ID: 20261007_10
Revises: 20261007_09
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_10"
down_revision = "20261007_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_forum_topics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_thread_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chat_id", "message_thread_id", name="uq_telegram_forum_topic"),
    )


def downgrade() -> None:
    op.drop_table("telegram_forum_topics")
