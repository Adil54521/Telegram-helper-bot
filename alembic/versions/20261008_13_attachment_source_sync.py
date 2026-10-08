"""attachment source identity and per-topic file sync

Revision ID: 20261008_13
Revises: 20261008_12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261008_13"
down_revision = "20261008_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    mode = postgresql.ENUM("OFF", "REVIEW", "AUTO", name="telegram_file_sync_mode")
    mode.create(op.get_bind(), checkfirst=True)
    op.add_column("telegram_topic_page_mappings", sa.Column("file_sync_mode", mode, server_default="OFF", nullable=False))
    op.add_column("page_attachments", sa.Column("telegram_source_post_id", sa.Integer(), nullable=True))
    op.add_column("page_attachments", sa.Column("telegram_source_chat_id", sa.BigInteger(), nullable=True))
    op.add_column("page_attachments", sa.Column("telegram_source_message_id", sa.BigInteger(), nullable=True))
    op.add_column("page_attachments", sa.Column("telegram_source_file_unique_id", sa.String(255), nullable=True))
    op.create_foreign_key("fk_attachment_telegram_source_post", "page_attachments", "telegram_content_posts", ["telegram_source_post_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_page_attachments_telegram_source_post_id", "page_attachments", ["telegram_source_post_id"])
    op.create_index("ix_attachment_telegram_source", "page_attachments", ["telegram_source_chat_id", "telegram_source_message_id"])
    op.create_table(
        "telegram_attachment_replacements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_post_id", sa.Integer(), sa.ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attachment_id", sa.Integer(), sa.ForeignKey("page_attachments.id", ondelete="SET NULL")),
        sa.Column("base_revision_id", sa.Integer(), sa.ForeignKey("bot_page_revisions.id", ondelete="SET NULL")),
        sa.Column("media_type", postgresql.ENUM(name="attachment_type", create_type=False), nullable=False),
        sa.Column("new_file_id", sa.String(512), nullable=False),
        sa.Column("new_file_unique_id", sa.String(255)),
        sa.Column("new_file_name", sa.String(255)),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), server_default="REVIEW", nullable=False),
        sa.Column("conflict_reason", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telegram_attachment_replacements_page_id", "telegram_attachment_replacements", ["page_id"])


def downgrade() -> None:
    op.drop_table("telegram_attachment_replacements")
    op.drop_index("ix_attachment_telegram_source", table_name="page_attachments")
    op.drop_index("ix_page_attachments_telegram_source_post_id", table_name="page_attachments")
    op.drop_constraint("fk_attachment_telegram_source_post", "page_attachments", type_="foreignkey")
    op.drop_column("page_attachments", "telegram_source_file_unique_id")
    op.drop_column("page_attachments", "telegram_source_message_id")
    op.drop_column("page_attachments", "telegram_source_chat_id")
    op.drop_column("page_attachments", "telegram_source_post_id")
    op.drop_column("telegram_topic_page_mappings", "file_sync_mode")
    postgresql.ENUM(name="telegram_file_sync_mode").drop(op.get_bind(), checkfirst=True)
