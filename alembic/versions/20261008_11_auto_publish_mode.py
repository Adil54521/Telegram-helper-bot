"""add per-topic Telegram processing mode

Revision ID: 20261008_11
Revises: 20261007_10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261008_11"
down_revision = "20261007_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    mode = postgresql.ENUM("MANUAL", "AUTO_DRAFT", "AUTO_PUBLISH", name="telegram_processing_mode")
    mode.create(op.get_bind(), checkfirst=True)
    op.add_column("telegram_topic_page_mappings", sa.Column("processing_mode", mode, nullable=True))
    op.add_column("telegram_topic_page_mappings", sa.Column("processing_mode_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text(
        "UPDATE telegram_topic_page_mappings "
        "SET processing_mode = CASE WHEN auto_draft_enabled THEN 'AUTO_DRAFT'::telegram_processing_mode ELSE 'MANUAL'::telegram_processing_mode END, "
        "processing_mode_enabled_at = created_at"
    ))
    op.alter_column("telegram_topic_page_mappings", "processing_mode", nullable=False, server_default="AUTO_DRAFT")
    op.alter_column("telegram_topic_page_mappings", "processing_mode_enabled_at", nullable=False, server_default=sa.text("now()"))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE telegram_topic_page_mappings SET auto_draft_enabled = (processing_mode <> 'MANUAL'::telegram_processing_mode)"
    ))
    op.drop_column("telegram_topic_page_mappings", "processing_mode_enabled_at")
    op.drop_column("telegram_topic_page_mappings", "processing_mode")
    postgresql.ENUM(name="telegram_processing_mode").drop(op.get_bind(), checkfirst=True)
