"""game roster: заявка сбора за игру, уведомления «отметились N игроков», сообщение состава

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-13 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("game_payments", sa.Column("roster_message_id", sa.BigInteger(), nullable=True))
    op.create_table(
        "game_payment_players",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("payment_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["game_payments.id"],
            name=op.f("fk_game_payment_players_payment_id_game_payments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_game_payment_players_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_payment_players")),
        sa.UniqueConstraint(
            "payment_id", "user_id", name=op.f("uq_game_payment_players_payment_id_user_id")
        ),
    )
    op.create_index(
        op.f("ix_game_payment_players_payment_id"), "game_payment_players", ["payment_id"]
    )
    op.create_table(
        "roster_notices",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("poll_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("players", sa.Integer(), nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_roster_notices_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["poll_id"],
            ["polls.id"],
            name=op.f("fk_roster_notices_poll_id_polls"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_roster_notices_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roster_notices")),
    )
    op.create_index(op.f("ix_roster_notices_event_id"), "roster_notices", ["event_id"])
    op.create_index(op.f("ix_roster_notices_poll_id"), "roster_notices", ["poll_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_roster_notices_poll_id"), table_name="roster_notices")
    op.drop_index(op.f("ix_roster_notices_event_id"), table_name="roster_notices")
    op.drop_table("roster_notices")
    op.drop_index(op.f("ix_game_payment_players_payment_id"), table_name="game_payment_players")
    op.drop_table("game_payment_players")
    op.drop_column("game_payments", "roster_message_id")
