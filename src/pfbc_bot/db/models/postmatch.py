"""Послематчевые анкеты, зафиксированные снимки и очередь доставки."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, IdMixin


class MatchSurvey(IdMixin, Base):
    __tablename__ = "post_match_surveys"

    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(16))
    scheduled_at: Mapped[datetime]
    opened_at: Mapped[datetime | None]
    reminder_at: Mapped[datetime | None]
    deadline: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    reason: Mapped[str | None]
    participants: Mapped[int] = mapped_column(default=0)
    responses: Mapped[int] = mapped_column(default=0)


class SurveyPlayer(IdMixin, Base):
    __tablename__ = "post_match_players"
    __table_args__ = (UniqueConstraint("survey_id", "user_id"),)
    survey_id: Mapped[int] = mapped_column(ForeignKey("post_match_surveys.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str]
    step: Mapped[int] = mapped_column(default=0)
    answers: Mapped[dict[str, Any]] = mapped_column(default=dict)
    submitted_at: Mapped[datetime | None]
    # Очередь приглашений, отзыва доступа и напоминаний переживает перезапуск.
    notice: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(default=0)
    reminded: Mapped[bool] = mapped_column(default=False)


class SurveyReport(IdMixin, Base):
    __tablename__ = "post_match_reports"
    family: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"))
    kind: Mapped[str]
    visibility: Mapped[str]
    snapshot: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    ready: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(default=0)


class ReportRecipient(IdMixin, Base):
    __tablename__ = "post_match_report_recipients"
    __table_args__ = (UniqueConstraint("report_id", "chat_id"),)
    report_id: Mapped[int] = mapped_column(ForeignKey("post_match_reports.id", ondelete="CASCADE"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    automatic: Mapped[bool]
    status: Mapped[str]
    attempts: Mapped[int] = mapped_column(default=0)
