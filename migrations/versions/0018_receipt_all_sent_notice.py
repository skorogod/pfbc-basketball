"""receipt_requests.all_sent_notice: «Все прислали» — снять кнопку при завершении сбора

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-14 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "receipt_requests",
        sa.Column("all_sent_notice", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("receipt_requests", "all_sent_notice")
