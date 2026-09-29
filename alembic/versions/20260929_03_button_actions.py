"""add button actions

Revision ID: 20260929_03
Revises: 20260929_02
"""

from alembic import op


revision = "20260929_03"
down_revision = "20260929_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE button_action ADD VALUE IF NOT EXISTS 'HOME'")
    op.execute("ALTER TYPE button_action ADD VALUE IF NOT EXISTS 'PHONE'")
    op.execute("ALTER TYPE button_action ADD VALUE IF NOT EXISTS 'EMAIL'")
    op.execute("ALTER TYPE button_action ADD VALUE IF NOT EXISTS 'TELEGRAM'")


def downgrade() -> None:
    # PostgreSQL не позволяет безопасно удалить отдельное значение ENUM
    # простым ALTER TYPE. Поэтому destructive downgrade здесь намеренно
    # не выполняется.
    pass