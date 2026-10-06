"""migrate the legacy channel queue to universal Telegram content

Revision ID: 20261006_07
Revises: 20261006_06
"""

from alembic import op
import sqlalchemy as sa


revision = "20261006_07"
down_revision = "20261006_06"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    return {item["name"] for item in _inspector().get_columns(table)}


def _drop_indexes(table: str) -> None:
    for index in _inspector().get_indexes(table):
        if not index.get("unique"):
            op.drop_index(index["name"], table_name=table)


def _drop_unique_constraints(table: str) -> None:
    for constraint in _inspector().get_unique_constraints(table):
        if constraint.get("name"):
            op.drop_constraint(constraint["name"], table, type_="unique")


def _rename_enum(old: str, new: str) -> None:
    bind = op.get_bind()
    query = sa.text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :name)")
    exists = bind.execute(query, {"name": old}).scalar_one()
    target_exists = bind.execute(query, {"name": new}).scalar_one()
    if exists and not target_exists:
        op.execute(sa.text(f'ALTER TYPE "{old}" RENAME TO "{new}"'))


def upgrade() -> None:
    tables = _tables()
    if "channel_posts" in tables and "telegram_content_posts" not in tables:
        op.rename_table("channel_posts", "telegram_content_posts")
    if "channel_post_media" in tables and "telegram_content_media" not in tables:
        op.rename_table("channel_post_media", "telegram_content_media")

    post_columns = _columns("telegram_content_posts")
    if "channel_id" in post_columns and "chat_id" not in post_columns:
        op.alter_column("telegram_content_posts", "channel_id", new_column_name="chat_id")
    if "channel_title" in post_columns and "chat_title" not in post_columns:
        op.alter_column("telegram_content_posts", "channel_title", new_column_name="chat_title")
    post_columns = _columns("telegram_content_posts")
    if "chat_type" not in post_columns:
        op.add_column("telegram_content_posts", sa.Column("chat_type", sa.String(16), nullable=True))
        op.execute(sa.text("UPDATE telegram_content_posts SET chat_type = 'channel' WHERE chat_type IS NULL"))
        op.alter_column("telegram_content_posts", "chat_type", nullable=False)
    if "message_thread_id" not in post_columns:
        op.add_column("telegram_content_posts", sa.Column("message_thread_id", sa.Integer(), nullable=True))

    media_columns = _columns("telegram_content_media")
    if "channel_post_id" in media_columns and "content_post_id" not in media_columns:
        op.alter_column("telegram_content_media", "channel_post_id", new_column_name="content_post_id")

    _rename_enum("channel_post_status", "telegram_content_status")
    _rename_enum("channel_media_type", "telegram_content_media_type")
    _drop_unique_constraints("telegram_content_media")
    _drop_unique_constraints("telegram_content_posts")
    _drop_indexes("telegram_content_media")
    _drop_indexes("telegram_content_posts")
    op.create_unique_constraint("uq_telegram_content_post_message", "telegram_content_posts", ["chat_id", "message_id"])
    op.create_unique_constraint("uq_telegram_content_media_message", "telegram_content_media", ["content_post_id", "source_message_id"])
    op.create_index("ix_telegram_content_posts_media_group_id", "telegram_content_posts", ["media_group_id"])
    op.create_index("ix_telegram_content_posts_status", "telegram_content_posts", ["status"])
    op.create_index("ix_telegram_content_posts_status_created", "telegram_content_posts", ["status", "created_at"])
    op.create_index("ix_telegram_content_posts_group", "telegram_content_posts", ["chat_id", "media_group_id", "message_thread_id"])
    op.create_index("ix_telegram_content_media_content_post_id", "telegram_content_media", ["content_post_id"])


def downgrade() -> None:
    _drop_unique_constraints("telegram_content_media")
    _drop_unique_constraints("telegram_content_posts")
    _drop_indexes("telegram_content_media")
    _drop_indexes("telegram_content_posts")

    post_columns = _columns("telegram_content_posts")
    if "message_thread_id" in post_columns:
        op.drop_column("telegram_content_posts", "message_thread_id")
    if "chat_type" in post_columns:
        op.drop_column("telegram_content_posts", "chat_type")
    if "chat_title" in post_columns and "channel_title" not in post_columns:
        op.alter_column("telegram_content_posts", "chat_title", new_column_name="channel_title")
    if "chat_id" in post_columns and "channel_id" not in post_columns:
        op.alter_column("telegram_content_posts", "chat_id", new_column_name="channel_id")
    media_columns = _columns("telegram_content_media")
    if "content_post_id" in media_columns and "channel_post_id" not in media_columns:
        op.alter_column("telegram_content_media", "content_post_id", new_column_name="channel_post_id")

    _rename_enum("telegram_content_status", "channel_post_status")
    _rename_enum("telegram_content_media_type", "channel_media_type")
    op.rename_table("telegram_content_media", "channel_post_media")
    op.rename_table("telegram_content_posts", "channel_posts")
    op.create_unique_constraint("uq_channel_post_message", "channel_posts", ["channel_id", "message_id"])
    op.create_unique_constraint("uq_channel_post_media_message", "channel_post_media", ["channel_post_id", "source_message_id"])
    op.create_index("ix_channel_posts_media_group_id", "channel_posts", ["media_group_id"])
    op.create_index("ix_channel_posts_status", "channel_posts", ["status"])
    op.create_index("ix_channel_posts_status_created", "channel_posts", ["status", "created_at"])
    op.create_index("ix_channel_post_media_channel_post_id", "channel_post_media", ["channel_post_id"])
