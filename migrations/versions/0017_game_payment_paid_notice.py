"""game_payments.paid_notice: сообщения «Все оплатили» — снять кнопку при завершении сбора

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-14 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "game_payments",
        sa.Column("paid_notice", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("game_payments", "paid_notice")
