"""roster_notices: одно уведомление на получателя по опросу (poll_id, user_id)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-13 23:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # возможные дубли (до уникальности) — оставляем самое раннее уведомление
    op.execute(
        "DELETE FROM roster_notices a USING roster_notices b "
        "WHERE a.poll_id = b.poll_id AND a.user_id = b.user_id AND a.id > b.id"
    )
    op.create_unique_constraint(
        op.f("uq_roster_notices_poll_id_user_id"), "roster_notices", ["poll_id", "user_id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_roster_notices_poll_id_user_id"), "roster_notices", type_="unique")
