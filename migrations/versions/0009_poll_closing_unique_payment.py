"""poll closed_at, one active game payment per match

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("polls", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    # дубли действующих сборов (следствие гонки) — оставляем последний
    op.execute(
        "UPDATE game_payments g SET cancelled_at = now() "
        "WHERE cancelled_at IS NULL AND EXISTS ("
        "SELECT 1 FROM game_payments o WHERE o.event_id = g.event_id "
        "AND o.cancelled_at IS NULL AND o.id > g.id)"
    )
    op.create_index(
        "uq_game_payments_active_event",
        "game_payments",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("cancelled_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_game_payments_active_event", table_name="game_payments")
    op.drop_column("polls", "closed_at")
