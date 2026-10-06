"""add persisted Telegram content-source moderation queue

Revision ID: 20261006_06
Revises: 20261005_05
"""

from alembic import op
import sqlalchemy as sa


revision = "20261006_06"
down_revision = "20261005_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    status = sa.Enum("NEW", "DRAFT_CREATED", "IGNORED", name="telegram_content_status")
    media_type = sa.Enum("PHOTO", "DOCUMENT", name="telegram_content_media_type")
    op.create_table(
        "telegram_content_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("media_group_id", sa.String(128)),
        sa.Column("message_thread_id", sa.Integer()),
        sa.Column("chat_type", sa.String(16), nullable=False),
        sa.Column("chat_title", sa.String(255)),
        sa.Column("status", status, nullable=False, server_default="NEW"),
        sa.Column("text", sa.Text()),
        sa.Column("caption", sa.Text()),
        sa.Column("telegram_created_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("processed_by", sa.BigInteger()),
        sa.Column("created_page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="SET NULL")),
        sa.Column("created_faq_id", sa.Integer(), sa.ForeignKey("faq_items.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_telegram_content_post_message"),
    )
    op.create_index("ix_telegram_content_posts_media_group_id", "telegram_content_posts", ["media_group_id"])
    op.create_index("ix_telegram_content_posts_status", "telegram_content_posts", ["status"])
    op.create_index("ix_telegram_content_posts_status_created", "telegram_content_posts", ["status", "created_at"])
    op.create_index("ix_telegram_content_posts_group", "telegram_content_posts", ["chat_id", "media_group_id", "message_thread_id"])
    op.create_table(
        "telegram_content_media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_post_id", sa.Integer(), sa.ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=False),
        sa.Column("type", media_type, nullable=False),
        sa.Column("telegram_file_id", sa.String(512), nullable=False),
        sa.Column("file_unique_id", sa.String(512)),
        sa.Column("file_name", sa.String(255)),
        sa.Column("file_size", sa.BigInteger()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("content_post_id", "source_message_id", name="uq_telegram_content_media_message"),
    )
    op.create_index("ix_telegram_content_media_content_post_id", "telegram_content_media", ["content_post_id"])


def downgrade() -> None:
    op.drop_index("ix_telegram_content_media_content_post_id", table_name="telegram_content_media")
    op.drop_table("telegram_content_media")
    op.drop_index("ix_telegram_content_posts_group", table_name="telegram_content_posts")
    op.drop_index("ix_telegram_content_posts_status_created", table_name="telegram_content_posts")
    op.drop_index("ix_telegram_content_posts_status", table_name="telegram_content_posts")
    op.drop_index("ix_telegram_content_posts_media_group_id", table_name="telegram_content_posts")
    op.drop_table("telegram_content_posts")
    sa.Enum(name="telegram_content_media_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="telegram_content_status").drop(op.get_bind(), checkfirst=True)
