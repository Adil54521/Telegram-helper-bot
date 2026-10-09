"""Telegram FAQ auto import target and knowledge jobs.

Revision ID: 20261008_14
Revises: 20261008_13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261008_14"
down_revision = "20261008_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    target = postgresql.ENUM("PAGE", "FAQ", name="telegram_content_target")
    target.create(op.get_bind(), checkfirst=True)
    op.add_column("telegram_topic_page_mappings", sa.Column("content_target", target, server_default="PAGE", nullable=False))
    op.alter_column("telegram_topic_page_mappings", "parent_page_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("ai_knowledge_sync_jobs", "page_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("ai_knowledge_sync_jobs", "revision_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("ai_knowledge_sync_jobs", sa.Column("faq_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_ai_knowledge_sync_job_faq", "ai_knowledge_sync_jobs", "faq_items", ["faq_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_ai_knowledge_sync_job_faq", "ai_knowledge_sync_jobs", ["faq_id"])
    op.create_check_constraint(
        "ck_ai_knowledge_sync_job_source", "ai_knowledge_sync_jobs",
        "(page_id IS NOT NULL AND faq_id IS NULL AND revision_id IS NOT NULL) OR "
        "(page_id IS NULL AND faq_id IS NOT NULL AND revision_id IS NULL)",
    )


def downgrade() -> None:
    # Refuse a lossy downgrade. Operators can switch/delete FAQ mappings and
    # finish FAQ jobs explicitly before retrying.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM telegram_topic_page_mappings WHERE content_target = 'FAQ')
             OR EXISTS (SELECT 1 FROM ai_knowledge_sync_jobs WHERE faq_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Cannot downgrade while Telegram FAQ mappings or FAQ knowledge jobs exist';
          END IF;
        END $$
    """)
    op.drop_constraint("ck_ai_knowledge_sync_job_source", "ai_knowledge_sync_jobs", type_="check")
    op.drop_constraint("uq_ai_knowledge_sync_job_faq", "ai_knowledge_sync_jobs", type_="unique")
    op.drop_constraint("fk_ai_knowledge_sync_job_faq", "ai_knowledge_sync_jobs", type_="foreignkey")
    op.drop_column("ai_knowledge_sync_jobs", "faq_id")
    op.alter_column("ai_knowledge_sync_jobs", "revision_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("ai_knowledge_sync_jobs", "page_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("telegram_topic_page_mappings", "parent_page_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("telegram_topic_page_mappings", "content_target")
    postgresql.ENUM(name="telegram_content_target").drop(op.get_bind(), checkfirst=True)
