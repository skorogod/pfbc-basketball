"""Послематчевые анкеты и очередь отчётов."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("opponent", sa.String(length=200), nullable=True))
    op.create_table(
        "post_match_surveys",
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("participants", sa.Integer(), nullable=False),
        sa.Column("responses", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_post_match_surveys_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post_match_surveys")),
        sa.UniqueConstraint("event_id", name=op.f("uq_post_match_surveys_event_id")),
    )
    op.create_table(
        "post_match_players",
        sa.Column("survey_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notice", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("reminded", sa.Boolean(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["survey_id"],
            ["post_match_surveys.id"],
            name=op.f("fk_post_match_players_survey_id_post_match_surveys"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_post_match_players_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post_match_players")),
        sa.UniqueConstraint(
            "survey_id", "user_id", name=op.f("uq_post_match_players_survey_id_user_id")
        ),
        sa.UniqueConstraint("token", name=op.f("uq_post_match_players_token")),
    )
    op.create_index(
        op.f("ix_post_match_players_user_id"), "post_match_players", ["user_id"], unique=False
    )
    op.create_table(
        "post_match_reports",
        sa.Column("family", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("squad_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("visibility", sa.String(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["squad_id"],
            ["squads.id"],
            name=op.f("fk_post_match_reports_squad_id_squads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post_match_reports")),
        sa.UniqueConstraint("key", name=op.f("uq_post_match_reports_key")),
    )
    op.create_index(
        op.f("ix_post_match_reports_family"), "post_match_reports", ["family"], unique=False
    )
    op.create_table(
        "post_match_report_recipients",
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["post_match_reports.id"],
            name=op.f("fk_post_match_report_recipients_report_id_post_match_reports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post_match_report_recipients")),
        sa.UniqueConstraint(
            "report_id", "chat_id", name=op.f("uq_post_match_report_recipients_report_id_chat_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("post_match_report_recipients")
    op.drop_table("post_match_reports")
    op.drop_table("post_match_players")
    op.drop_table("post_match_surveys")
    op.drop_column("events", "opponent")
