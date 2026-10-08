"""polls superseded, unique counted duty

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("polls", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("uq_polls_type_ref_type_ref_id", "polls", type_="unique")
    op.create_index(
        "uq_polls_active_ref",
        "polls",
        ["type", "ref_type", "ref_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    # дубли засчитываемых назначений (следствие гонок) — оставляем первое
    op.execute(
        "UPDATE duty_assignments d SET status = 'replaced' "
        "WHERE status IN ('assigned', 'done') AND EXISTS ("
        "SELECT 1 FROM duty_assignments o WHERE o.training_id = d.training_id "
        "AND o.user_id = d.user_id AND o.status IN ('assigned', 'done') AND o.id < d.id)"
    )
    op.create_index(
        "uq_duty_assignments_counted_user",
        "duty_assignments",
        ["training_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('assigned', 'done')"),
    )


def downgrade() -> None:
    op.drop_index("uq_duty_assignments_counted_user", table_name="duty_assignments")
    op.drop_index("uq_polls_active_ref", table_name="polls")
    op.execute("DELETE FROM polls WHERE superseded_at IS NOT NULL")
    op.create_unique_constraint(
        "uq_polls_type_ref_type_ref_id", "polls", ["type", "ref_type", "ref_id"]
    )
    op.drop_column("polls", "superseded_at")
