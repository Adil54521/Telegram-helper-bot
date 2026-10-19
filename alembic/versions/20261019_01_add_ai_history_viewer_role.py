"""add ai history viewer role

Revision ID: 20261019_01
Revises: da70d16a1c81
Create Date: 2026-10-19 10:26:55.930887
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '20261019_01'
down_revision: Union[str, None] = 'da70d16a1c81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute(
        "ALTER TYPE admin_role "
        "ADD VALUE IF NOT EXISTS 'AI_HISTORY_VIEWER'"
    )


def downgrade() -> None:
    # PostgreSQL не поддерживает простое DROP VALUE для ENUM.
    # Откат значения требует пересоздания типа и проверки данных.
    pass

