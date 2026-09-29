"""Add RU/KK translations, selected language and page attachments.

Revision ID: 20260929_02
Revises: 20260929_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260929_02"
down_revision = "20260929_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("telegram_users", sa.Column("content_language_code", sa.String(8), nullable=True))
    op.create_index("ix_telegram_users_content_language_code", "telegram_users", ["content_language_code"])
    language = postgresql.ENUM("RU", "KK", name="language_code")
    language.create(op.get_bind(), checkfirst=True)
    language_ref = postgresql.ENUM("RU", "KK", name="language_code", create_type=False)
    attachment_type = postgresql.ENUM("PHOTO", "DOCUMENT", name="attachment_type")
    attachment_type.create(op.get_bind(), checkfirst=True)
    attachment_ref = postgresql.ENUM("PHOTO", "DOCUMENT", name="attachment_type", create_type=False)

    op.create_table("bot_page_revision_translations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("bot_page_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language_code", language_ref, nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("revision_id", "language_code", name="uq_page_revision_language"))
    op.create_index("ix_bot_page_revision_translations_revision_id", "bot_page_revision_translations", ["revision_id"])
    op.create_table("bot_button_translations",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("button_id", sa.Integer(), sa.ForeignKey("bot_buttons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language_code", language_ref, nullable=False), sa.Column("text", sa.String(128), nullable=False),
        sa.UniqueConstraint("button_id", "language_code", name="uq_button_language"))
    op.create_index("ix_bot_button_translations_button_id", "bot_button_translations", ["button_id"])
    op.create_table("faq_item_translations",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("faq_id", sa.Integer(), sa.ForeignKey("faq_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language_code", language_ref, nullable=False), sa.Column("question", sa.String(500), nullable=False), sa.Column("answer", sa.Text(), nullable=False),
        sa.UniqueConstraint("faq_id", "language_code", name="uq_faq_language"))
    op.create_index("ix_faq_item_translations_faq_id", "faq_item_translations", ["faq_id"])
    op.create_table("page_attachments",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("revision_id", sa.Integer(), sa.ForeignKey("bot_page_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", attachment_ref, nullable=False), sa.Column("telegram_file_id", sa.String(512), nullable=False), sa.Column("file_name", sa.String(255)),
        sa.Column("caption_ru", sa.String(1024)), sa.Column("caption_kk", sa.String(1024)), sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_page_attachments_revision_id", "page_attachments", ["revision_id"])

    op.execute("INSERT INTO bot_page_revision_translations (revision_id, language_code, title, text) SELECT id, 'RU', title, text FROM bot_page_revisions")
    op.execute("INSERT INTO bot_button_translations (button_id, language_code, text) SELECT id, 'RU', text FROM bot_buttons")
    op.execute("INSERT INTO faq_item_translations (faq_id, language_code, question, answer) SELECT id, 'RU', question, answer FROM faq_items")


def downgrade() -> None:
    op.drop_table("page_attachments")
    op.drop_table("faq_item_translations")
    op.drop_table("bot_button_translations")
    op.drop_table("bot_page_revision_translations")
    postgresql.ENUM(name="attachment_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="language_code").drop(op.get_bind(), checkfirst=True)
    op.drop_index("ix_telegram_users_content_language_code", table_name="telegram_users")
    op.drop_column("telegram_users", "content_language_code")
