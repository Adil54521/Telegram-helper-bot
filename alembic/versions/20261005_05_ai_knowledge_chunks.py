"""add semantic knowledge chunks

Revision ID: 20261005_05
Revises: 20260930_04
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "20261005_05"
down_revision = "20260930_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "ai_knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.Integer()),
        sa.Column("language_code", sa.String(8), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint(
            "source_type", "source_id", "revision_id", "language_code", "chunk_index",
            name="uq_ai_knowledge_chunk_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_ai_knowledge_chunks_source_type", "ai_knowledge_chunks", ["source_type"])
    op.create_index("ix_ai_knowledge_chunks_source_id", "ai_knowledge_chunks", ["source_id"])
    op.create_index("ix_ai_knowledge_chunks_language_code", "ai_knowledge_chunks", ["language_code"])
    op.create_index("ix_ai_knowledge_chunk_source", "ai_knowledge_chunks", ["source_type", "source_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_knowledge_chunk_source", table_name="ai_knowledge_chunks")
    op.drop_index("ix_ai_knowledge_chunks_language_code", table_name="ai_knowledge_chunks")
    op.drop_index("ix_ai_knowledge_chunks_source_id", table_name="ai_knowledge_chunks")
    op.drop_index("ix_ai_knowledge_chunks_source_type", table_name="ai_knowledge_chunks")
    op.drop_table("ai_knowledge_chunks")

