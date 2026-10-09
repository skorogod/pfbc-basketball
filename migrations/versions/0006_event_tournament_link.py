"""event tournament link

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("tournament_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_events_tournament_id_events"),
        "events",
        "events",
        ["tournament_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_events_tournament_id"), "events", ["tournament_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_events_tournament_id"), table_name="events")
    op.drop_constraint(op.f("fk_events_tournament_id_events"), "events", type_="foreignkey")
    op.drop_column("events", "tournament_id")
