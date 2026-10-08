"""duty cancelled status

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # назначения на отменённые тренировки не засчитываются как дежурство;
    # прошедшие assigned → done проставит job complete_duty при первом запуске
    op.execute(
        "UPDATE duty_assignments SET status = 'cancelled' "
        "WHERE status = 'assigned' "
        "AND training_id IN (SELECT id FROM trainings WHERE status = 'cancelled')"
    )


def downgrade() -> None:
    op.execute("UPDATE duty_assignments SET status = 'assigned' WHERE status = 'cancelled'")
