"""Подтверждённая заявка матча и решение об оплате."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("roster_player_ids", sa.ARRAY(sa.BigInteger())))
    op.add_column("events", sa.Column("roster_message_id", sa.BigInteger()))
    op.add_column("events", sa.Column("roster_chat_id", sa.BigInteger()))
    op.add_column("events", sa.Column("payment_required", sa.Boolean()))
    op.execute("""
        UPDATE events e SET roster_player_ids = (
            SELECT array_agg(r.user_id ORDER BY r.id) FROM game_payment_players r
            WHERE r.payment_id = p.id
        ), roster_message_id = p.roster_message_id, roster_chat_id = (
            SELECT chat_id FROM polls WHERE ref_type = 'game_payment' AND ref_id = p.id
            ORDER BY id DESC LIMIT 1
        ), payment_required = true
        FROM game_payments p WHERE p.event_id = e.id AND p.cancelled_at IS NULL
    """)


def downgrade() -> None:
    for column in ("payment_required", "roster_chat_id", "roster_message_id", "roster_player_ids"):
        op.drop_column("events", column)
