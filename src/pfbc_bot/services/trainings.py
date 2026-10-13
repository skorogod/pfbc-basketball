"""Расписание тренировок, разовые тренировки, отмены и переносы (ФТ-2).

Отмена и перенос возможны только до начала тренировки. При переносе команда получает новые
сообщения: о переносе — в группу, новый опрос — на новое время (старый закрывается), снятым
дежурным — в личку; дежурные назначаются заново по ответам на новый опрос.
"""

import html
from datetime import datetime, timedelta

from pfbc_bot.db.models import Poll, Training, TrainingSchedule, User
from pfbc_bot.domain.dates import dates_for_weekday, format_local, local_to_utc, to_local, utcnow
from pfbc_bot.domain.enums import (
    ACTIVE_TRAINING_STATUSES,
    DutyStatus,
    PollType,
    TopicPurpose,
    TrainingSource,
    TrainingStatus,
)
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.schemas.trainings import ScheduleCreate, ScheduleDTO, TrainingCreate, TrainingDTO
from pfbc_bot.services.common import (
    AfterCommit,
    deferred_to_squad,
    run_after_commit,
    send_direct,
    squad_tz,
)
from pfbc_bot.services.duty import duty_key
from pfbc_bot.services.errors import Conflict, NotFound
from pfbc_bot.services.permissions import require
from pfbc_bot.services.polls import poll_key
from pfbc_bot.services.ports import Notifier
from pfbc_bot.texts import ru

MATERIALIZE_DAYS = 14
TRAINING_REF = "training"


class TrainingService:
    def __init__(self, uow_factory: UowFactory, notifier: Notifier) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier

    # --- шаблоны расписания ---
    async def add_schedule(
        self, actor: UserContext, data: ScheduleCreate, *, now: datetime | None = None
    ) -> ScheduleDTO:
        require(actor, data.squad_id, Permission.SCHEDULE)
        async with self._uow_factory() as uow:
            schedule = await uow.trainings.add_schedule(TrainingSchedule(**data.model_dump()))
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="schedule.add",
                entity="training_schedule",
                entity_id=schedule.id,
            )
            await self._materialize_schedule(uow, schedule, now or utcnow())
            await uow.commit()
            return ScheduleDTO.model_validate(schedule)

    async def get_schedule(self, actor: UserContext, schedule_id: int) -> ScheduleDTO:
        """Строка расписания — для вопроса «удалить?»: уже на этом шаге право проверяется,
        иначе подделанная кнопка показала бы расписание чужого состава."""
        async with self._uow_factory() as uow:
            schedule = await uow.trainings.get_schedule(schedule_id)
            if schedule is None or not schedule.is_active:
                raise NotFound("Запись расписания не найдена")
            require(actor, schedule.squad_id, Permission.SCHEDULE)
            return ScheduleDTO.model_validate(schedule)

    @staticmethod
    def check_schedule_access(actor: UserContext, squad_id: int) -> None:
        """Право менять расписание состава — до начала ввода строки."""
        require(actor, squad_id, Permission.SCHEDULE)

    async def list_schedules(self, squad_id: int) -> list[ScheduleDTO]:
        async with self._uow_factory() as uow:
            return [
                ScheduleDTO.model_validate(s) for s in await uow.trainings.list_schedules(squad_id)
            ]

    async def remove_schedule(
        self, actor: UserContext, schedule_id: int, *, now: datetime | None = None
    ) -> None:
        now = now or utcnow()
        async with self._uow_factory() as uow:
            schedule = await uow.trainings.get_schedule(schedule_id)
            if schedule is None or not schedule.is_active:
                raise NotFound("Запись расписания не найдена")
            require(actor, schedule.squad_id, Permission.SCHEDULE)
            schedule.is_active = False
            after = []
            for training in await uow.trainings.list_future_from_schedule(schedule_id, now):
                after += await self._cancel(uow, training, announce=False, now=now)
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="schedule.remove",
                entity="training_schedule",
                entity_id=schedule_id,
            )
            await uow.commit()
        await run_after_commit(after)

    # --- материализация ---
    async def materialize(self, *, now: datetime | None = None) -> int:
        """Создаёт тренировки по шаблонам на MATERIALIZE_DAYS вперёд (идемпотентно)."""
        now = now or utcnow()
        created = 0
        async with self._uow_factory() as uow:
            for schedule in await uow.trainings.list_schedules():
                created += await self._materialize_schedule(uow, schedule, now)
            await uow.commit()
        return created

    @staticmethod
    async def _materialize_schedule(
        uow: UnitOfWork, schedule: TrainingSchedule, now: datetime
    ) -> int:
        squad = await uow.squads.get(schedule.squad_id)
        if squad is None or not squad.is_active:
            return 0
        tz = squad_tz(squad)
        today = to_local(now, tz).date()
        created = 0
        for day in dates_for_weekday(today, MATERIALIZE_DAYS, schedule.weekday):
            if schedule.valid_from and day < schedule.valid_from:
                continue
            if schedule.valid_to and day > schedule.valid_to:
                continue
            starts_at = local_to_utc(day, schedule.start_time, tz)
            if starts_at <= now:
                continue
            created += await uow.trainings.insert_from_schedule_if_absent(schedule, day, starts_at)
        return created

    # --- конкретные тренировки ---
    async def upcoming(
        self, squad_id: int, *, now: datetime | None = None, days: int = MATERIALIZE_DAYS
    ) -> list[TrainingDTO]:
        now = now or utcnow()
        async with self._uow_factory() as uow:
            trainings = await uow.trainings.list_between(
                now, now + timedelta(days=days), squad_id=squad_id
            )
            return [TrainingDTO.model_validate(t) for t in trainings]

    async def recent_and_upcoming(
        self, squad_id: int, *, now: datetime | None = None
    ) -> list[TrainingDTO]:
        """Для сводки посещаемости: от суток назад до недели вперёд."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            trainings = await uow.trainings.list_between(
                now - timedelta(days=1), now + timedelta(days=7), squad_id=squad_id
            )
            return [TrainingDTO.model_validate(t) for t in trainings]

    async def get(
        self, actor: UserContext, training_id: int, permission: Permission = Permission.SCHEDULE
    ) -> TrainingDTO:
        """Тренировка для действия с правом permission (отмена и перенос — расписание, опрос —
        опрос): право проверяется уже на шаге вопроса, а не только при подтверждении."""
        async with self._uow_factory() as uow:
            training = await uow.trainings.get(training_id)
            if training is None:
                raise NotFound("Тренировка не найдена")
            require(actor, training.squad_id, permission)
            return TrainingDTO.model_validate(training)

    async def add_one_off(
        self, actor: UserContext, data: TrainingCreate, *, now: datetime | None = None
    ) -> TrainingDTO:
        require(actor, data.squad_id, Permission.SCHEDULE)
        if data.starts_at <= (now or utcnow()):
            raise Conflict(ru.PAST_DATE)
        async with self._uow_factory() as uow:
            training = await uow.trainings.add(
                Training(
                    **data.model_dump(), source=TrainingSource.MANUAL, status=TrainingStatus.PLANNED
                )
            )
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="training.add",
                entity="training",
                entity_id=training.id,
            )
            await uow.commit()
            return TrainingDTO.model_validate(training)

    async def cancel(
        self, actor: UserContext, training_id: int, *, now: datetime | None = None
    ) -> TrainingDTO:
        async with self._uow_factory() as uow:
            training = await self._get_upcoming(uow, training_id, now or utcnow())
            require(actor, training.squad_id, Permission.SCHEDULE)
            after = await self._cancel(uow, training, announce=True, now=now or utcnow())
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="training.cancel",
                entity="training",
                entity_id=training_id,
            )
            await uow.commit()
            await run_after_commit(after)
            return TrainingDTO.model_validate(training)

    async def move(
        self,
        actor: UserContext,
        training_id: int,
        new_starts_at: datetime,
        *,
        now: datetime | None = None,
    ) -> TrainingDTO:
        now = now or utcnow()
        if new_starts_at <= now:
            raise Conflict(ru.PAST_DATE)
        async with self._uow_factory() as uow:
            training = await self._get_upcoming(uow, training_id, now)
            require(actor, training.squad_id, Permission.SCHEDULE)
            tz = squad_tz(await uow.squads.get(training.squad_id))
            old = training.starts_at
            training.starts_at = new_starts_at
            training.status = TrainingStatus.MOVED
            after = [await self._reset_poll(uow, training, now)]
            removed = await self._reset_duties(uow, training)
            after.append(
                await deferred_to_squad(
                    uow,
                    self._notifier,
                    training.squad_id,
                    TopicPurpose.TRAININGS,
                    ru.TRAINING_MOVED_GROUP.format(
                        old=format_local(old, tz),
                        new=format_local(new_starts_at, tz),
                        venue=html.escape(training.venue),
                    ),
                )
            )
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="training.move",
                entity="training",
                entity_id=training_id,
                payload={"from": old.isoformat(), "to": new_starts_at.isoformat()},
            )
            await uow.commit()
            await run_after_commit(after)  # сообщения — после снятия блокировки
            text = ru.DUTY_MOVED_DM.format(
                old=format_local(old, tz), new=format_local(new_starts_at, tz)
            )
            for user in removed:
                await send_direct(self._notifier, user, text)
            await uow.commit()  # dm_available
            return TrainingDTO.model_validate(training)

    @staticmethod
    async def _get_upcoming(uow: UnitOfWork, training_id: int, now: datetime) -> Training:
        """Тренировка под блокировкой (параллельно с заменой дежурных); только до начала."""
        training = await uow.trainings.get(training_id, for_update=True)
        if training is None or training.status not in ACTIVE_TRAINING_STATUSES:
            raise NotFound("Тренировка не найдена или уже отменена")
        if training.starts_at <= now:
            raise Conflict(ru.TRAINING_ALREADY_STARTED)
        return training

    async def _reset_poll(self, uow: UnitOfWork, training: Training, now: datetime) -> AfterCommit:
        """Опрос на старое время закрывается (в Telegram — после commit); новый опубликует
        планировщик по новому времени."""
        poll = await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training.id)
        if poll is not None:
            await uow.polls.supersede(poll, now)
        await uow.system.release_reminder(poll_key(training.id))
        return self._stop_poll(poll)

    def _stop_poll(self, poll: Poll | None) -> AfterCommit:
        async def stop() -> object:
            if poll is None:
                return None
            return await self._notifier.stop_poll(poll.chat_id, poll.message_id)

        return stop

    @staticmethod
    async def _reset_duties(uow: UnitOfWork, training: Training) -> list[User]:
        """Дежурные на старое время снимаются; назначение повторится по новому опросу.
        Возвращает снятых — им отправляется личное сообщение."""
        assigned = [
            a.user_id
            for a in await uow.trainings.list_duties(training.id)
            if a.status == DutyStatus.ASSIGNED
        ]
        await uow.trainings.cancel_duties(training.id)
        await uow.system.release_reminder(duty_key(training.id))
        training.duty_chat_id = training.duty_message_id = None  # объявление будет новым
        return await uow.users.list_by_ids(assigned)

    async def _cancel(
        self, uow: UnitOfWork, training: Training, *, announce: bool, now: datetime
    ) -> list[AfterCommit]:
        """Отмена: дежурства снимаются, опрос закрывается (ответы больше ни на что не влияют).
        Возвращает отправки в Telegram — выполнить после commit."""
        training.status = TrainingStatus.CANCELLED
        await uow.trainings.cancel_duties(training.id)
        poll = await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training.id)
        after = []
        if poll is not None:
            await uow.polls.supersede(poll, now)
            after.append(self._stop_poll(poll))
        if announce or poll is not None:  # если опрос уже был — команду нужно предупредить
            tz = squad_tz(await uow.squads.get(training.squad_id))
            after.append(
                await deferred_to_squad(
                    uow,
                    self._notifier,
                    training.squad_id,
                    TopicPurpose.TRAININGS,
                    ru.TRAINING_CANCELLED_GROUP.format(
                        when=format_local(training.starts_at, tz),
                        venue=html.escape(training.venue),
                    ),
                )
            )
        return after
