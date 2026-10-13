"""events.division: дивизион лиги у турнира (связь с матчами mtgame)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-13 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("division", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "division")
