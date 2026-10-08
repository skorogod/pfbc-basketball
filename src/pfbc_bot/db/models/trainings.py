from datetime import date, datetime, time

from sqlalchemy import BigInteger, ForeignKey, Index, SmallInteger, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, CreatedAtMixin, IdMixin, str_enum
from pfbc_bot.domain.enums import DutyStatus, TrainingSource, TrainingStatus


class TrainingSchedule(IdMixin, CreatedAtMixin, Base):
    """Шаблон регулярной тренировки состава (день недели, время, зал)."""

    __tablename__ = "training_schedules"

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)  # 0 = понедельник
    start_time: Mapped[time]
    duration_minutes: Mapped[int]
    venue: Mapped[str] = mapped_column(String(255))
    valid_from: Mapped[date | None]
    valid_to: Mapped[date | None]
    is_active: Mapped[bool] = mapped_column(default=True)


class Training(IdMixin, CreatedAtMixin, Base):
    """Конкретная тренировка: материализована из шаблона или создана вручную."""

    __tablename__ = "trainings"
    __table_args__ = (
        # идемпотентная материализация: одна тренировка на шаблон и дату (перенос дату не меняет)
        UniqueConstraint("schedule_id", "scheduled_for"),
        Index("ix_trainings_squad_starts", "squad_id", "starts_at"),
    )

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"))
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_schedules.id", ondelete="SET NULL")
    )
    scheduled_for: Mapped[date | None]
    starts_at: Mapped[datetime]
    duration_minutes: Mapped[int]
    venue: Mapped[str] = mapped_column(String(255))
    status: Mapped[TrainingStatus] = mapped_column(
        str_enum(TrainingStatus), default=TrainingStatus.PLANNED
    )
    source: Mapped[TrainingSource] = mapped_column(str_enum(TrainingSource))
    # объявление о дежурных в группе — редактируется при заменах
    duty_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    duty_message_id: Mapped[int | None]


class DutyAssignment(IdMixin, CreatedAtMixin, Base):
    """Назначение дежурного по залу на тренировку."""

    __tablename__ = "duty_assignments"
    __table_args__ = (
        Index("ix_duty_assignments_user_status", "user_id", "status"),
        # защита от гонок: один и тот же игрок не может быть засчитан дважды на тренировку
        Index(
            "uq_duty_assignments_counted_user",
            "training_id",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('assigned', 'done')"),
        ),
    )

    training_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[DutyStatus] = mapped_column(str_enum(DutyStatus), default=DutyStatus.ASSIGNED)
    assigned_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )  # None — назначено ботом
    replaced_at: Mapped[datetime | None]
