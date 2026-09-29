"""Initial schema."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260929_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    admin_role = sa.Enum("SUPER_ADMIN", "ADMIN", "EDITOR", name="admin_role")
    page_status = sa.Enum("DRAFT", "PUBLISHED", "ARCHIVED", name="page_status")
    media_type = sa.Enum("PHOTO", "DOCUMENT", name="media_type")
    button_action = sa.Enum("PAGE", "URL", "BACK", name="button_action")
    broadcast_status = sa.Enum("DRAFT", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", name="broadcast_status")
    op.create_table("telegram_users", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("telegram_id", sa.BigInteger(), nullable=False), sa.Column("username", sa.String(64)), sa.Column("first_name", sa.String(255), nullable=False), sa.Column("last_name", sa.String(255)), sa.Column("language_code", sa.String(16)), sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("telegram_id"))
    op.create_index("ix_telegram_users_telegram_id", "telegram_users", ["telegram_id"])
    op.create_index("ix_telegram_users_last_seen_at", "telegram_users", ["last_seen_at"])
    op.create_table("admins", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("telegram_id", sa.BigInteger(), nullable=False), sa.Column("role", admin_role, nullable=False), sa.Column("display_name", sa.String(255)), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("telegram_id"))
    op.create_index("ix_admins_telegram_id", "admins", ["telegram_id"])
    op.create_table("bot_pages", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("key", sa.String(64), nullable=False), sa.Column("status", page_status, nullable=False), sa.Column("position", sa.Integer(), nullable=False, server_default="0"), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_by", sa.BigInteger()), sa.Column("updated_by", sa.BigInteger()), sa.Column("published_revision_id", sa.Integer()), sa.Column("draft_revision_id", sa.Integer()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("key"))
    op.create_index("ix_bot_pages_key", "bot_pages", ["key"])
    op.create_index("ix_bot_pages_is_active", "bot_pages", ["is_active"])
    op.create_table("bot_page_revisions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("text", sa.Text(), nullable=False), sa.Column("media_type", media_type), sa.Column("media_file_id", sa.String(512)), sa.Column("created_by", sa.BigInteger()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_bot_page_revisions_page_id", "bot_page_revisions", ["page_id"])
    op.create_foreign_key("fk_page_published_revision", "bot_pages", "bot_page_revisions", ["published_revision_id"], ["id"])
    op.create_foreign_key("fk_page_draft_revision", "bot_pages", "bot_page_revisions", ["draft_revision_id"], ["id"])
    op.create_table("bot_buttons", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=False), sa.Column("text", sa.String(128), nullable=False), sa.Column("action_type", button_action, nullable=False), sa.Column("target", sa.String(512)), sa.Column("position", sa.Integer(), nullable=False, server_default="0"), sa.Column("row", sa.Integer(), nullable=False, server_default="0"), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_buttons_page_order", "bot_buttons", ["page_id", "row", "position"])
    op.create_table("faq_items", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("question", sa.String(500), nullable=False), sa.Column("answer", sa.Text(), nullable=False), sa.Column("position", sa.Integer(), nullable=False, server_default="0"), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("audit_logs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("admin_id", sa.BigInteger(), nullable=False), sa.Column("action", sa.String(64), nullable=False), sa.Column("entity_type", sa.String(64), nullable=False), sa.Column("entity_id", sa.String(64), nullable=False), sa.Column("old_data", postgresql.JSONB()), sa.Column("new_data", postgresql.JSONB()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_audit_logs_admin_id", "audit_logs", ["admin_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_table("page_views", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False), sa.Column("page_id", sa.Integer(), sa.ForeignKey("bot_pages.id", ondelete="CASCADE"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_page_views_user_id", "page_views", ["user_id"])
    op.create_index("ix_page_views_page_id", "page_views", ["page_id"])
    op.create_index("ix_page_views_created_at", "page_views", ["created_at"])
    op.create_table("broadcasts", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("text", sa.Text(), nullable=False), sa.Column("media_file_id", sa.String(512)), sa.Column("status", broadcast_status, nullable=False), sa.Column("created_by", sa.BigInteger(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("total", sa.Integer(), nullable=False, server_default="0"), sa.Column("sent", sa.Integer(), nullable=False, server_default="0"), sa.Column("failed", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for table in ("broadcasts", "page_views", "audit_logs", "faq_items", "bot_buttons"):
        op.drop_table(table)
    op.drop_constraint("fk_page_draft_revision", "bot_pages", type_="foreignkey")
    op.drop_constraint("fk_page_published_revision", "bot_pages", type_="foreignkey")
    op.drop_table("bot_page_revisions")
    op.drop_table("bot_pages")
    op.drop_table("admins")
    op.drop_table("telegram_users")
    for enum_name in ("broadcast_status", "button_action", "media_type", "page_status", "admin_role"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
