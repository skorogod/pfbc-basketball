"""Оплата тренировок по квитанциям mos.ru (этап 3)."""

from datetime import date, datetime, time

from sqlalchemy import ForeignKey, SmallInteger, String, UniqueConstraint
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
