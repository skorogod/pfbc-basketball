"""events.pinned_*: закреплённый пост о событии в группе

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-13 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("pinned_chat_id", sa.BigInteger(), nullable=True))
    op.add_column("events", sa.Column("pinned_message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "pinned_message_id")
    op.drop_column("events", "pinned_chat_id")
