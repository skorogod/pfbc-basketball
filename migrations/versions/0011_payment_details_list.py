"""several payment details per user: object -> list with ids

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-13 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE users SET payment_details = jsonb_build_array("
        "payment_details || jsonb_build_object('id', substr(md5(random()::text), 1, 8))) "
        "WHERE jsonb_typeof(payment_details) = 'object'"
    )


def downgrade() -> None:
    # оставляем первые реквизиты: в старом формате — только одни
    op.execute(
        "UPDATE users SET payment_details = (payment_details -> 0) - 'id' "
        "WHERE jsonb_typeof(payment_details) = 'array' "
        "AND jsonb_array_length(payment_details) > 0"
    )
    op.execute(
        "UPDATE users SET payment_details = NULL "
        "WHERE jsonb_typeof(payment_details) = 'array'"
    )
