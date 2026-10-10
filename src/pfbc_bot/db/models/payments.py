"""Оплата тренировок по квитанциям mos.ru (этап 3) и сбор денег за игры — это разные вещи."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, Numeric, SmallInteger, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, CreatedAtMixin, IdMixin, str_enum
from pfbc_bot.domain.enums import ReceiptStatus


class PaymentReminderConfig(IdMixin, Base):
    """Расписание оповещений «пришлите квитанцию»: день месяца или разовая дата."""

    __tablename__ = "payment_reminders_config"

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    day_of_month: Mapped[int | None] = mapped_column(SmallInteger)
    at_time: Mapped[time]
    once_at: Mapped[datetime | None]
    is_active: Mapped[bool] = mapped_column(default=True)
    next_run_at: Mapped[datetime | None]


class PaymentPeriod(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "payment_periods"

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    starts_on: Mapped[date]
    ends_on: Mapped[date]
    announced_at: Mapped[datetime | None]


class PaymentReceipt(IdMixin, Base):
    __tablename__ = "payment_receipts"
    __table_args__ = (UniqueConstraint("period_id", "user_id"),)

    period_id: Mapped[int] = mapped_column(ForeignKey("payment_periods.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    file_id: Mapped[str | None] = mapped_column(String(255))
    file_type: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[ReceiptStatus] = mapped_column(str_enum(ReceiptStatus))
    received_at: Mapped[datetime | None]
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class GamePayment(IdMixin, CreatedAtMixin, Base):
    """Сбор денег за игру: сумма, реквизиты, дедлайн и опрос «Оплатил» в группе.
    После дедлайна бот напоминает ответившим «Буду (игрок)», кто не отметил оплату."""

    __tablename__ = "game_payments"
    __table_args__ = (
        # действующий сбор на матч — один
        Index(
            "uq_game_payments_active_event",
            "event_id",
            unique=True,
            postgresql_where=text("cancelled_at IS NULL"),
        ),
    )

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    deadline_at: Mapped[datetime | None]
    details: Mapped[dict[str, Any]]  # снимок реквизитов на момент сбора
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # кому переводят деньги (реквизиты — его); создать сбор может и админ за тренера
    collector_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # заменён новым сбором по той же игре или игра удалена — напоминаний нет
    cancelled_at: Mapped[datetime | None]


class ReceiptRequest(IdMixin, CreatedAtMixin, Base):
    """Опрос «прислали квитанцию за тренировки тренеру?» за месяц: по расписанию или вручную.
    После срока бот напоминает тем, кто не отметил «Отправил». Один на состав в месяц."""

    __tablename__ = "receipt_requests"
    __table_args__ = (UniqueConstraint("squad_id", "month"),)

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    month: Mapped[date]  # первое число месяца (по часовому поясу состава)
    deadline_at: Mapped[datetime]
    # None — опубликован по расписанию
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
