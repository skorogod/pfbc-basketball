"""topic purpose games -> match (опрос участия в игре и сбор денег за неё)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE squad_topics SET purpose = 'match' WHERE purpose = 'games'")


def downgrade() -> None:
    op.execute("UPDATE squad_topics SET purpose = 'games' WHERE purpose = 'match'")
