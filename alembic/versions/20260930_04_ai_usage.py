"""add persistent AI usage analytics

Revision ID: 20260930_04
Revises: 20260929_03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_04"
down_revision = "20260929_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_type", sa.String(32), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("retrieval_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gemini_called", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_price_per_1m", sa.Numeric(18, 10), nullable=False, server_default="0"),
        sa.Column("output_price_per_1m", sa.Numeric(18, 10), nullable=False, server_default="0"),
        sa.Column("input_cost_usd", sa.Numeric(18, 10), nullable=False, server_default="0"),
        sa.Column("output_cost_usd", sa.Numeric(18, 10), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Numeric(18, 10), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error_type", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_usage_user_id", "ai_usage", ["user_id"])
    op.create_index("ix_ai_usage_request_type", "ai_usage", ["request_type"])
    op.create_index("ix_ai_usage_created_at", "ai_usage", ["created_at"])
    op.create_index("ix_ai_usage_type_created", "ai_usage", ["request_type", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_type_created", table_name="ai_usage")
    op.drop_index("ix_ai_usage_created_at", table_name="ai_usage")
    op.drop_index("ix_ai_usage_request_type", table_name="ai_usage")
    op.drop_index("ix_ai_usage_user_id", table_name="ai_usage")
    op.drop_table("ai_usage")
