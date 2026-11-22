"""Перенос ожидающих доставок между форматами послематчевых отчётов."""

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def _move_pending(source: str, target: str) -> None:
    db = op.get_bind()
    reports = db.execute(
        sa.text("""
        SELECT r.* FROM post_match_reports r
        WHERE kind = :source AND expires_at > CURRENT_TIMESTAMP AND EXISTS (
            SELECT 1 FROM post_match_report_recipients d
            WHERE d.report_id = r.id AND d.status = 'pending'
        ) ORDER BY r.id
    """),
        {"source": source},
    ).mappings()
    for report in reports:
        snapshot = report["snapshot"]
        squad = report["squad_id"]
        scope = [
            squad,
            target,
            report["visibility"],
            snapshot["player"],
            snapshot["period"],
            None if snapshot["history"] else snapshot["games"][0]["event"],
        ]
        family = hashlib.sha256(json.dumps(scope).encode()).hexdigest()
        key = hashlib.sha256(
            json.dumps([squad, target, snapshot], sort_keys=True).encode()
        ).hexdigest()
        new_id = db.execute(
            sa.text("""
            INSERT INTO post_match_reports
                (family, key, squad_id, kind, visibility, snapshot, created_at, expires_at, ready, attempts)
            SELECT :family, :key, squad_id, :target, visibility, snapshot, created_at, expires_at, false, 0
            FROM post_match_reports WHERE id = :old_id
            ON CONFLICT (key) DO UPDATE SET
                expires_at = greatest(post_match_reports.expires_at, excluded.expires_at),
                attempts = 0
            RETURNING id
        """),
            {"family": family, "key": key, "target": target, "old_id": report["id"]},
        ).scalar_one()
        ids = {"old_id": report["id"], "new_id": new_id}
        db.execute(
            sa.text("""
            INSERT INTO post_match_report_recipients (report_id, chat_id, automatic, status, attempts)
            SELECT :new_id, chat_id, automatic, status, attempts
            FROM post_match_report_recipients WHERE report_id = :old_id AND status = 'pending'
            ON CONFLICT (report_id, chat_id) DO UPDATE SET
                status = 'pending', automatic = excluded.automatic, attempts = excluded.attempts
            WHERE post_match_report_recipients.status IN ('failed', 'revoked')
        """),
            ids,
        )
        db.execute(
            sa.text("""
            DELETE FROM post_match_report_recipients WHERE report_id = :old_id AND status = 'pending'
        """),
            ids,
        )


def upgrade() -> None:
    _move_pending("docx", "pdf")


def downgrade() -> None:
    _move_pending("pdf", "docx")
