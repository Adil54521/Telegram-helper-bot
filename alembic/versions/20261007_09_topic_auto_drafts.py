"""topic mappings and restart-safe automatic drafts

Revision ID: 20261007_09
Revises: 20261006_08
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_09"
down_revision = "20261006_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("telegram_content_posts", sa.Column("auto_process_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("telegram_content_posts", sa.Column("auto_processing_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("telegram_content_posts", sa.Column("auto_last_error", sa.String(255), nullable=True))
    op.create_index("ix_telegram_content_posts_auto_process_after", "telegram_content_posts", ["auto_process_after"])
    op.create_table(
        "telegram_import_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("auto_draft_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("auto_draft_enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settle_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column("notify_admins", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chat_id", name="uq_telegram_import_settings_chat"),
        sa.CheckConstraint("settle_seconds BETWEEN 15 AND 300", name="ck_telegram_import_settle_seconds"),
    )
    op.create_table(
        "telegram_topic_page_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_thread_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("auto_draft_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("auto_attach_materials", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chat_id", "message_thread_id", name="uq_telegram_topic_page_mapping"),
    )
    op.create_index("ix_telegram_topic_page_mappings_parent_page_id", "telegram_topic_page_mappings", ["parent_page_id"])
    op.create_table(
        "telegram_auto_draft_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_post_id", sa.Integer(), sa.ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("child_page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("parent_page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mapping_id", sa.Integer(), sa.ForeignKey("telegram_topic_page_mappings.id", ondelete="SET NULL"), nullable=True),
        sa.Column("button_id", sa.Integer(), sa.ForeignKey("bot_buttons.id", ondelete="SET NULL"), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telegram_auto_draft_links_parent_page_id", "telegram_auto_draft_links", ["parent_page_id"])


def downgrade() -> None:
    op.drop_table("telegram_auto_draft_links")
    op.drop_index("ix_telegram_topic_page_mappings_parent_page_id", table_name="telegram_topic_page_mappings")
    op.drop_table("telegram_topic_page_mappings")
    op.drop_table("telegram_import_settings")
    op.drop_index("ix_telegram_content_posts_auto_process_after", table_name="telegram_content_posts")
    op.drop_column("telegram_content_posts", "auto_last_error")
    op.drop_column("telegram_content_posts", "auto_processing_attempts")
    op.drop_column("telegram_content_posts", "auto_process_after")
