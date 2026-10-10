"""receipt requests: monthly poll «sent the training receipt to the coach»

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "receipt_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("squad_id", sa.BigInteger(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["squad_id"],
            ["squads.id"],
            name=op.f("fk_receipt_requests_squad_id_squads"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_receipt_requests_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipt_requests")),
        sa.UniqueConstraint("squad_id", "month", name=op.f("uq_receipt_requests_squad_id_month")),
    )
    op.create_index(op.f("ix_receipt_requests_squad_id"), "receipt_requests", ["squad_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_receipt_requests_squad_id"), table_name="receipt_requests")
    op.drop_table("receipt_requests")
