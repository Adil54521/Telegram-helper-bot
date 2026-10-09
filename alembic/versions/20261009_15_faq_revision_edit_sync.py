"""FAQ revision history and Telegram FAQ edit sync.

Revision ID: 20261009_15
Revises: 20261008_14
"""
from alembic import op
import sqlalchemy as sa

revision = "20261009_15"
down_revision = "20261008_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "faq_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("faq_id", sa.Integer(), sa.ForeignKey("faq_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("question_ru", sa.String(500), nullable=False),
        sa.Column("answer_ru", sa.Text(), nullable=False),
        sa.Column("question_kk", sa.String(500)),
        sa.Column("answer_kk", sa.Text()),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_post_id", sa.Integer(), sa.ForeignKey("telegram_content_posts.id", ondelete="SET NULL")),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("faq_id", "version", name="uq_faq_revision_version"),
    )
    op.create_index("ix_faq_revisions_faq_id", "faq_revisions", ["faq_id"])
    op.create_index("ix_faq_revisions_source_post_id", "faq_revisions", ["source_post_id"])
    op.create_index("ix_faq_revisions_created_at", "faq_revisions", ["created_at"])
    op.add_column("faq_items", sa.Column("current_revision_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_faq_current_revision", "faq_items", "faq_revisions", ["current_revision_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_faq_items_current_revision_id", "faq_items", ["current_revision_id"])

    op.execute("""
        INSERT INTO faq_revisions
            (faq_id, version, question_ru, answer_ru, question_kk, answer_kk,
             source_type, content_fingerprint, created_at)
        SELECT f.id, 1,
               COALESCE(ru.question, f.question), COALESCE(ru.answer, f.answer),
               kk.question, kk.answer, 'MANUAL',
               md5(COALESCE(ru.question, f.question) || chr(31) || COALESCE(ru.answer, f.answer) || chr(31) ||
                   COALESCE(kk.question, '') || chr(31) || COALESCE(kk.answer, '')) ||
               md5(COALESCE(kk.answer, '') || chr(31) || COALESCE(ru.answer, f.answer)),
               COALESCE(f.updated_at, f.created_at, now())
        FROM faq_items f
        LEFT JOIN faq_item_translations ru ON ru.faq_id=f.id AND ru.language_code='RU'
        LEFT JOIN faq_item_translations kk ON kk.faq_id=f.id AND kk.language_code='KK'
    """)
    op.execute("""
        UPDATE faq_items f SET current_revision_id=r.id
        FROM faq_revisions r WHERE r.faq_id=f.id AND r.version=1
    """)
    op.drop_constraint("ck_ai_knowledge_sync_job_source", "ai_knowledge_sync_jobs", type_="check")
    op.add_column("ai_knowledge_sync_jobs", sa.Column("faq_revision_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_ai_knowledge_sync_job_faq_revision", "ai_knowledge_sync_jobs", "faq_revisions", ["faq_revision_id"], ["id"], ondelete="CASCADE")
    op.execute("""
        UPDATE ai_knowledge_sync_jobs j SET faq_revision_id=f.current_revision_id
        FROM faq_items f WHERE j.faq_id=f.id
    """)
    op.create_check_constraint(
        "ck_ai_knowledge_sync_job_source", "ai_knowledge_sync_jobs",
        "(page_id IS NOT NULL AND faq_id IS NULL AND revision_id IS NOT NULL AND faq_revision_id IS NULL) OR "
        "(page_id IS NULL AND faq_id IS NOT NULL AND revision_id IS NULL AND faq_revision_id IS NOT NULL)",
    )

    op.create_table(
        "telegram_faq_sync_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_post_id", sa.Integer(), sa.ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("faq_id", sa.Integer(), sa.ForeignKey("faq_items.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("last_synced_revision_id", sa.Integer(), sa.ForeignKey("faq_revisions.id", ondelete="SET NULL")),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("last_edit_date", sa.DateTime(timezone=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telegram_faq_sync_links_faq_id", "telegram_faq_sync_links", ["faq_id"])
    op.create_table(
        "telegram_faq_edit_pending",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_post_id", sa.Integer(), sa.ForeignKey("telegram_content_posts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("faq_id", sa.Integer(), sa.ForeignKey("faq_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_revision_id", sa.Integer(), sa.ForeignKey("faq_revisions.id", ondelete="SET NULL")),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("question_ru", sa.String(500)),
        sa.Column("answer_ru", sa.Text()),
        sa.Column("question_kk", sa.String(500)),
        sa.Column("answer_kk", sa.Text()),
        sa.Column("source_edit_date", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), server_default="REVIEW", nullable=False),
        sa.Column("conflict_reason", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telegram_faq_edit_pending_faq_id", "telegram_faq_edit_pending", ["faq_id"])


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM faq_revisions WHERE version > 1)
             OR EXISTS (SELECT 1 FROM telegram_faq_sync_links)
             OR EXISTS (SELECT 1 FROM telegram_faq_edit_pending) THEN
            RAISE EXCEPTION 'Cannot downgrade: FAQ revision or Telegram FAQ sync history exists';
          END IF;
        END $$
    """)
    op.drop_index("ix_telegram_faq_edit_pending_faq_id", table_name="telegram_faq_edit_pending")
    op.drop_table("telegram_faq_edit_pending")
    op.drop_index("ix_telegram_faq_sync_links_faq_id", table_name="telegram_faq_sync_links")
    op.drop_table("telegram_faq_sync_links")
    op.drop_constraint("ck_ai_knowledge_sync_job_source", "ai_knowledge_sync_jobs", type_="check")
    op.drop_constraint("fk_ai_knowledge_sync_job_faq_revision", "ai_knowledge_sync_jobs", type_="foreignkey")
    op.drop_column("ai_knowledge_sync_jobs", "faq_revision_id")
    op.create_check_constraint(
        "ck_ai_knowledge_sync_job_source", "ai_knowledge_sync_jobs",
        "(page_id IS NOT NULL AND faq_id IS NULL AND revision_id IS NOT NULL) OR "
        "(page_id IS NULL AND faq_id IS NOT NULL AND revision_id IS NULL)",
    )
    op.drop_index("ix_faq_items_current_revision_id", table_name="faq_items")
    op.drop_constraint("fk_faq_current_revision", "faq_items", type_="foreignkey")
    op.drop_column("faq_items", "current_revision_id")
    op.drop_index("ix_faq_revisions_created_at", table_name="faq_revisions")
    op.drop_index("ix_faq_revisions_source_post_id", table_name="faq_revisions")
    op.drop_index("ix_faq_revisions_faq_id", table_name="faq_revisions")
    op.drop_table("faq_revisions")
