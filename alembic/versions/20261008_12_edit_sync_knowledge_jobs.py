"""Telegram edit sync and restart-safe knowledge jobs

Revision ID: 20261008_12
Revises: 20261008_11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261008_12"
down_revision = "20261008_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    mode = postgresql.ENUM("OFF", "REVIEW", "AUTO", name="telegram_edit_sync_mode")
    mode.create(op.get_bind(), checkfirst=True)
    op.add_column("telegram_topic_page_mappings", sa.Column("edit_sync_mode", mode, server_default="OFF", nullable=False))
    op.add_column("telegram_auto_draft_links", sa.Column("last_synced_revision_id", sa.Integer(), nullable=True))
    op.add_column("telegram_auto_draft_links", sa.Column("source_fingerprint", sa.String(64), nullable=True))
    op.create_foreign_key("fk_auto_draft_last_synced_revision", "telegram_auto_draft_links", "bot_page_revisions", ["last_synced_revision_id"], ["id"], ondelete="SET NULL")
    op.create_table(
        "telegram_edit_pending",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_post_id", sa.Integer(), sa.ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_revision_id", sa.Integer(), sa.ForeignKey("bot_page_revisions.id", ondelete="SET NULL")),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("title_ru", sa.String(255)), sa.Column("text_ru", sa.Text()),
        sa.Column("title_kk", sa.String(255)), sa.Column("text_kk", sa.Text()),
        sa.Column("status", sa.String(24), server_default="REVIEW", nullable=False),
        sa.Column("conflict_reason", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telegram_edit_pending_page_id", "telegram_edit_pending", ["page_id"])
    op.create_table(
        "ai_knowledge_sync_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("bot_page_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_knowledge_sync_jobs_status", "ai_knowledge_sync_jobs", ["status"])
    op.create_index("ix_ai_knowledge_sync_jobs_next_attempt_at", "ai_knowledge_sync_jobs", ["next_attempt_at"])


def downgrade() -> None:
    op.drop_table("ai_knowledge_sync_jobs")
    op.drop_table("telegram_edit_pending")
    op.drop_constraint("fk_auto_draft_last_synced_revision", "telegram_auto_draft_links", type_="foreignkey")
    op.drop_column("telegram_auto_draft_links", "source_fingerprint")
    op.drop_column("telegram_auto_draft_links", "last_synced_revision_id")
    op.drop_column("telegram_topic_page_mappings", "edit_sync_mode")
    postgresql.ENUM(name="telegram_edit_sync_mode").drop(op.get_bind(), checkfirst=True)
