from datetime import date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import DutyAssignment, Training, TrainingSchedule
from pfbc_bot.domain.enums import (
    ACTIVE_TRAINING_STATUSES,
    COUNTED_DUTY_STATUSES,
    DutyStatus,
    TrainingSource,
)


class TrainingsRepository:
    """Шаблоны расписания, тренировки и назначения дежурных."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- шаблоны ---
    async def add_schedule(self, schedule: TrainingSchedule) -> TrainingSchedule:
        self._session.add(schedule)
        await self._session.flush()
        return schedule

    async def get_schedule(self, schedule_id: int) -> TrainingSchedule | None:
        return await self._session.get(TrainingSchedule, schedule_id)

    async def list_schedules(self, squad_id: int | None = None) -> list[TrainingSchedule]:
        stmt = select(TrainingSchedule).where(TrainingSchedule.is_active.is_(True))
        if squad_id is not None:
            stmt = stmt.where(TrainingSchedule.squad_id == squad_id)
        stmt = stmt.order_by(TrainingSchedule.weekday, TrainingSchedule.start_time)
        return list(await self._session.scalars(stmt))

    # --- тренировки ---
    async def get(self, training_id: int, *, for_update: bool = False) -> Training | None:
        """for_update — блокировка строки до конца транзакции: изменения дежурств и самой
        тренировки (отказ, замена, перенос, отмена) выполняются строго по очереди."""
        if not for_update:
            return await self._session.get(Training, training_id)
        stmt = (
            select(Training)
            .where(Training.id == training_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(stmt)).first()

    async def add(self, training: Training) -> Training:
        self._session.add(training)
        await self._session.flush()
        return training

    async def insert_from_schedule_if_absent(
        self, schedule: TrainingSchedule, scheduled_for: date, starts_at: datetime
    ) -> bool:
        stmt = (
            insert(Training)
            .values(
                squad_id=schedule.squad_id,
                schedule_id=schedule.id,
                scheduled_for=scheduled_for,
                starts_at=starts_at,
                duration_minutes=schedule.duration_minutes,
                venue=schedule.venue,
                source=TrainingSource.SCHEDULE,
            )
            .on_conflict_do_nothing(index_elements=[Training.schedule_id, Training.scheduled_for])
            .returning(Training.id)
        )
        return (await self._session.scalar(stmt)) is not None

    async def list_between(
        self,
        start: datetime,
        end: datetime,
        *,
        squad_id: int | None = None,
        active_only: bool = True,
    ) -> list[Training]:
        stmt = select(Training).where(Training.starts_at >= start, Training.starts_at < end)
        if squad_id is not None:
            stmt = stmt.where(Training.squad_id == squad_id)
        if active_only:
            stmt = stmt.where(Training.status.in_(ACTIVE_TRAINING_STATUSES))
        return list(await self._session.scalars(stmt.order_by(Training.starts_at)))

    async def list_future_from_schedule(self, schedule_id: int, after: datetime) -> list[Training]:
        stmt = select(Training).where(
            Training.schedule_id == schedule_id,
            Training.starts_at > after,
            Training.status.in_(ACTIVE_TRAINING_STATUSES),
        )
        return list(await self._session.scalars(stmt))

    # --- дежурства ---
    async def get_duty(self, assignment_id: int, *, refresh: bool = False) -> DutyAssignment | None:
        """refresh — перечитать из БД (после блокировки тренировки), а не из кеша сессии."""
        return await self._session.get(DutyAssignment, assignment_id, populate_existing=refresh)

    async def add_duty(self, assignment: DutyAssignment) -> DutyAssignment:
        self._session.add(assignment)
        await self._session.flush()
        return assignment

    async def list_duties(self, training_id: int) -> list[DutyAssignment]:
        stmt = (
            select(DutyAssignment)
            .where(DutyAssignment.training_id == training_id)
            .order_by(DutyAssignment.id)
        )
        return list(await self._session.scalars(stmt))

    async def upcoming_duties_for_user(
        self, user_id: int, squad_id: int, after: datetime
    ) -> list[DutyAssignment]:
        """Действующие назначения пользователя на ещё не начавшиеся тренировки состава."""
        stmt = (
            select(DutyAssignment)
            .join(Training, Training.id == DutyAssignment.training_id)
            .where(
                DutyAssignment.user_id == user_id,
                DutyAssignment.status == DutyStatus.ASSIGNED,
                Training.squad_id == squad_id,
                Training.starts_at > after,
                Training.status.in_(ACTIVE_TRAINING_STATUSES),
            )
        )
        return list(await self._session.scalars(stmt))

    async def active_duty_for_user(self, training_id: int, user_id: int) -> DutyAssignment | None:
        stmt = select(DutyAssignment).where(
            DutyAssignment.training_id == training_id,
            DutyAssignment.user_id == user_id,
            DutyAssignment.status == DutyStatus.ASSIGNED,
        )
        return (await self._session.scalars(stmt)).first()

    async def complete_duties(self, now: datetime) -> int:
        """Назначения на начавшиеся неотменённые тренировки: assigned → done."""
        started = select(Training.id).where(
            Training.starts_at <= now, Training.status.in_(ACTIVE_TRAINING_STATUSES)
        )
        stmt = (
            update(DutyAssignment)
            .where(
                DutyAssignment.status == DutyStatus.ASSIGNED,
                DutyAssignment.training_id.in_(started),
            )
            .values(status=DutyStatus.DONE)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def cancel_duties(self, training_id: int) -> None:
        """Тренировку отменили или перенесли: назначения не засчитываются (assigned → cancelled)."""
        await self._session.execute(
            update(DutyAssignment)
            .where(
                DutyAssignment.training_id == training_id,
                DutyAssignment.status == DutyStatus.ASSIGNED,
            )
            .values(status=DutyStatus.CANCELLED)
            .execution_options(synchronize_session=False)
        )

    async def last_duty_at(self, squad_id: int, user_ids: list[int]) -> dict[int, datetime]:
        """Дата последнего фактического дежурства каждого пользователя в составе."""
        if not user_ids:
            return {}
        stmt = (
            select(DutyAssignment.user_id, func.max(Training.starts_at))
            .join(Training, Training.id == DutyAssignment.training_id)
            .where(
                Training.squad_id == squad_id,
                DutyAssignment.user_id.in_(user_ids),
                DutyAssignment.status.in_(COUNTED_DUTY_STATUSES),
            )
            .group_by(DutyAssignment.user_id)
        )
        return {user_id: last for user_id, last in (await self._session.execute(stmt)).all()}
