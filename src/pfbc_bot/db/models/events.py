from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Text, false, text
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, CreatedAtMixin, IdMixin, str_enum
from pfbc_bot.domain.enums import EventType


class Event(IdMixin, CreatedAtMixin, Base):
    """Событие журнала состава (ФТ-9)."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_squad_starts", "squad_id", "starts_at"),
        # без даты может быть только турнир (дата старта ещё неизвестна)
        CheckConstraint("starts_at IS NOT NULL OR type = 'tournament'", name="dated"),
    )

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"))
    type: Mapped[EventType] = mapped_column(str_enum(EventType))
    title: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime | None]
    duration_minutes: Mapped[int] = mapped_column(default=90, server_default=text("90"))
    # турнир: длительность в днях или сезон (лига ABL) — без точной продолжительности
    duration_days: Mapped[int | None]
    is_season: Mapped[bool] = mapped_column(default=False, server_default=false())
    # турнир: стоимость одной игры (вся сумма) — для сбора денег с игроков, по желанию
    game_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    place: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    notify_group: Mapped[bool] = mapped_column(default=False)
    match_id: Mapped[int | None] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), unique=True
    )
    # матч, сыгранный в рамках турнира (связь настраивает админ)
    tournament_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), index=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime | None]
