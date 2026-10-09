"""user phone and payment details, game payments, tournament game price

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-09 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))
    op.add_column(
        "users",
        sa.Column("payment_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("events", sa.Column("game_price", sa.Numeric(10, 2), nullable=True))
    op.create_table(
        "game_payments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("squad_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("collector_user_id", sa.BigInteger(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["squad_id"],
            ["squads.id"],
            name=op.f("fk_game_payments_squad_id_squads"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_game_payments_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_game_payments_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["collector_user_id"],
            ["users.id"],
            name=op.f("fk_game_payments_collector_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_payments")),
    )
    op.create_index(op.f("ix_game_payments_squad_id"), "game_payments", ["squad_id"])
    op.create_index(op.f("ix_game_payments_event_id"), "game_payments", ["event_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_game_payments_event_id"), table_name="game_payments")
    op.drop_index(op.f("ix_game_payments_squad_id"), table_name="game_payments")
    op.drop_table("game_payments")
    op.drop_column("events", "game_price")
    op.drop_column("users", "payment_details")
    op.drop_column("users", "phone")
