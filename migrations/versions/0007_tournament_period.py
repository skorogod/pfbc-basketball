"""tournament period: optional start date, days or season

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("events", "starts_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.add_column("events", sa.Column("duration_days", sa.Integer(), nullable=True))
    op.add_column(
        "events",
        sa.Column("is_season", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_events_dated"), "events", "starts_at IS NOT NULL OR type = 'tournament'"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_events_dated"), "events", type_="check")
    op.execute("DELETE FROM events WHERE starts_at IS NULL")
    op.drop_column("events", "is_season")
    op.drop_column("events", "duration_days")
    op.alter_column(
        "events", "starts_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
