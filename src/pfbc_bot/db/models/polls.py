from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, CreatedAtMixin, IdMixin, str_enum
from pfbc_bot.domain.enums import PollType


class Poll(IdMixin, CreatedAtMixin, Base):
    """Опрос, опубликованный ботом (неанонимный — чтобы получать poll_answer)."""

    __tablename__ = "polls"
    __table_args__ = (
        # один действующий опрос на объект; устаревшие (после переноса тренировки) — в истории
        Index(
            "uq_polls_active_ref",
            "type",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )

    type: Mapped[PollType] = mapped_column(str_enum(PollType))
    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    ref_type: Mapped[str | None] = mapped_column(String(32))  # training / match / ...
    ref_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_poll_id: Mapped[str] = mapped_column(String(64), unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int]
    question: Mapped[str] = mapped_column(String(300))
    options: Mapped[list[Any]]
    closes_at: Mapped[datetime | None]
    # тренировку перенесли — опрос закрыт, ответы в нём больше ни на что не влияют
    superseded_at: Mapped[datetime | None]
    # голосование закрыто по сроку (closes_at): ответы сохранены и учитываются в сводках
    closed_at: Mapped[datetime | None]


class PollAnswer(IdMixin, Base):
    """Последний ответ пользователя на опрос. Пустой option_ids — голос отозван."""

    __tablename__ = "poll_answers"
    __table_args__ = (UniqueConstraint("poll_id", "user_id"),)

    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    option_ids: Mapped[list[Any]]
    answered_at: Mapped[datetime]
