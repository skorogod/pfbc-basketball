"""Назначение дежурных по залу (ФТ-3).

Как это выглядит для людей:
- в топике «Тренировки» — объявление о дежурных без кнопок. При любой замене бот пишет новое
  сообщение с упоминанием нового дежурного (ответом на объявление), чтобы его точно увидели, а
  само объявление правит — оно всегда актуально;
- дежурный получает назначение в личку с кнопкой «Не могу дежурить»;
- капитан (если его нет — тренер) управляет дежурствами в личке: замена следующим по очереди
  или ручной выбор. Любое изменение — с подтверждением. Если назначенный не получил ни личного
  сообщения, ни упоминания в группе, капитан видит пометку «не получил уведомление»;
- на раннюю тренировку (опрос накануне вечером) дежурные назначаются утром в duty_morning_time;
- после начала тренировки назначение становится «done». Если дежурного экстренно заменили,
  а бот не знал, капитан, тренер или админ исправляет запись в течение FIX_HORIZON.

Все изменения дежурств тренировки выполняются под блокировкой её строки: одновременные отказ
в опросе, кнопка дежурного и кнопка капитана не приводят к двойной замене.
"""

import html
import random
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pfbc_bot import callbacks
from pfbc_bot.db.models import DutyAssignment, Training, User
from pfbc_bot.domain.dates import duty_assign_at, format_local, utcnow
from pfbc_bot.domain.duty import DutyCandidate, select_duty
from pfbc_bot.domain.enums import (
    ACTIVE_TRAINING_STATUSES,
    COUNTED_DUTY_STATUSES,
    TRAINING_POLL_YES,
    DutyStatus,
    MembershipStatus,
    PollType,
    SquadRole,
    TopicPurpose,
)
from pfbc_bot.logging import get_logger
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.settings import SquadSettings
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.common import (
    notify_staff,
    resolve_destination,
    send_direct,
    squad_tz,
    to_dto,
)
from pfbc_bot.services.errors import Conflict, NotFound, PermissionDenied
from pfbc_bot.services.ports import InlineButton, Keyboard, Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)

TRAINING_REF = "training"
DUTY_MANAGERS = (SquadRole.CAPTAIN, SquadRole.COACH)
MAX_PICK_BUTTONS = 40
MANAGE_HORIZON = timedelta(days=7)
FIX_HORIZON = timedelta(days=7)  # сколько после тренировки можно исправить запись о дежурстве


@dataclass(frozen=True, slots=True)
class Prompt:
    """Сообщение с кнопками, которое роутер показывает пользователю (вопрос, список и т. п.)."""

    text: str
    keyboard: Keyboard = field(default_factory=list)


def duty_key(training_id: int) -> str:
    """Ключ reminder_log назначения дежурных (снимается при переносе — назначат заново)."""
    return f"duty:{training_id}"


def _button(text: str, action: str, *args: int) -> InlineButton:
    return InlineButton(text, callback_data=callbacks.pack(callbacks.DUTY, action, *args))


def _picked(user: User, notified: bool) -> str:
    """Ответ капитану после назначения; предупреждение, если дежурный ничего не получил."""
    name = html.escape(to_dto(user).display_name)
    text = ru.DUTY_PICKED.format(name=name)
    return text if notified else text + ru.DUTY_NOT_NOTIFIED_CAPTAIN.format(name=name)


def _confirm_keyboard(confirm_text: str, action: str, *args: int) -> Keyboard:
    return [[_button(confirm_text, action, *args), _button(ru.BTN_CANCEL, callbacks.DUTY_CANCEL)]]


class DutyService:
    def __init__(
        self, uow_factory: UowFactory, notifier: Notifier, *, rng: random.Random | None = None
    ) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier
        self._rng = rng or random.Random()

    # --- плановое назначение ---
    async def assign_due(self, *, now: datetime | None = None) -> int:
        """Назначает дежурных, когда подошло время (duty_assign_at): за duty_lead_hours до начала,
        на раннюю тренировку — утром. Ждёт, пока опубликован опрос: кандидаты — ответившие «Да».

        Две фазы: сначала отметка и назначения фиксируются в БД, затем рассылка. Сбой рассылки
        не приводит к повторному объявлению на следующем тике."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            training_ids = [
                t.id for t in await uow.trainings.list_between(now, now + timedelta(days=1))
            ]
        assigned = 0
        for training_id in training_ids:
            async with self._uow_factory() as uow:
                training = await uow.trainings.get(training_id, for_update=True)
                if training is None or training.status not in ACTIVE_TRAINING_STATUSES:
                    continue
                settings = await load_squad_settings(uow, training.squad_id)
                if not settings.features.duty:
                    continue
                tz = squad_tz(await uow.squads.get(training.squad_id))
                assign_at = duty_assign_at(
                    starts_at=training.starts_at,
                    tz=tz,
                    poll_time=settings.training_poll_time,
                    duty_lead=timedelta(hours=settings.duty_lead_hours),
                    early_until=settings.early_training_until,
                    morning_time=settings.duty_morning_time,
                    arrive_before=timedelta(minutes=settings.duty_arrive_minutes),
                )
                if assign_at > now:
                    continue
                if settings.features.training_polls and not await uow.polls.get_for_ref(
                    PollType.TRAINING, TRAINING_REF, training.id
                ):
                    continue  # опроса ещё нет — некого выбирать; ждём следующего тика
                if not await uow.system.claim_reminder(duty_key(training.id), "duty"):
                    continue
                chosen = select_duty(
                    await self._candidates(uow, training),
                    count=settings.duty_count,
                    now=now,
                    grace_days=settings.duty_newcomer_grace_days,
                    rng=self._rng,
                )
                assignments = [
                    await uow.trainings.add_duty(
                        DutyAssignment(training_id=training.id, user_id=user_id)
                    )
                    for user_id in chosen
                ]
                await uow.commit()
                await self._announce_initial(uow, training, settings, assignments)
                await uow.commit()
                log.info("duty_assigned", training_id=training.id, users=chosen)
                assigned += 1
        return assigned

    async def _announce_initial(
        self,
        uow: UnitOfWork,
        training: Training,
        settings: SquadSettings,
        assignments: list[DutyAssignment],
    ) -> None:
        """Объявление в группе, личные сообщения дежурным, сводка капитану."""
        announced = await self._publish_announcement(uow, training, settings)
        not_notified: set[int] = set()
        for assignment in assignments:
            delivered = await self._notify_assigned(uow, training, assignment, settings)
            if not delivered and not announced:
                not_notified.add(assignment.user_id)
        if len(assignments) < settings.duty_count:
            await self._notify_shortage(uow, training, len(assignments), settings.duty_count)
        else:
            await self._send_staff_summary(uow, training, not_notified)

    # --- тренировка состоялась ---
    async def complete_due(self, *, now: datetime | None = None) -> int:
        """Назначения на начавшиеся неотменённые тренировки засчитываются (assigned → done)."""
        async with self._uow_factory() as uow:
            completed = await uow.trainings.complete_duties(now or utcnow())
            await uow.commit()
        if completed:
            log.info("duty_completed", count=completed)
        return completed

    # --- отказ самого дежурного ---
    async def handle_decline(
        self, training_id: int, user_id: int, *, now: datetime | None = None
    ) -> None:
        """Дежурный ответил «не приду» в опросе — автоматическая замена, капитану уведомление."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            training = await uow.trainings.get(training_id, for_update=True)
            if (
                training is None
                or training.status not in ACTIVE_TRAINING_STATUSES
                or training.starts_at <= now
            ):
                return
            assignment = await uow.trainings.active_duty_for_user(training_id, user_id)
            if assignment is None:
                return
            await self._replace_and_report(
                uow, training, assignment, now, ru.DUTY_AUTO_REPLACED_CAPTAIN
            )
            await uow.commit()

    async def confirm_decline(
        self, actor: UserContext, assignment_id: int, *, now: datetime | None = None
    ) -> Prompt:
        async with self._uow_factory() as uow:
            _, training = await self._own_assignment(uow, actor, assignment_id, now or utcnow())
            when = await self._when(uow, training)
        return Prompt(
            ru.DUTY_CONFIRM_DECLINE.format(when=when),
            _confirm_keyboard(
                ru.DUTY_BTN_CONFIRM_DECLINE, callbacks.DUTY_DECLINE_OK, assignment_id
            ),
        )

    async def decline(
        self, actor: UserContext, assignment_id: int, *, now: datetime | None = None
    ) -> str:
        """Дежурный сам отказался (кнопка «Не могу дежурить»); ответ в опросе остаётся «Да»."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            assignment, training = await self._own_assignment(
                uow, actor, assignment_id, now, lock=True
            )
            await self._replace_and_report(
                uow, training, assignment, now, ru.DUTY_SELF_DECLINED_CAPTAIN
            )
            await uow.commit()
        return ru.DUTY_DECLINED

    # --- управление капитаном/тренером ---
    async def manager_prompts(
        self, actor: UserContext, squad_id: int, *, now: datetime | None = None
    ) -> list[Prompt]:
        """Команда /duty: ближайшие тренировки с дежурными и кнопками управления,
        затем недавние прошедшие — с кнопками исправления записи."""
        self._require_manager(actor, squad_id)
        now = now or utcnow()
        async with self._uow_factory() as uow:
            prompts = []
            for training in await uow.trainings.list_between(
                now, now + MANAGE_HORIZON, squad_id=squad_id
            ):
                if await uow.trainings.list_duties(training.id):
                    prompts.append(await self._staff_prompt(uow, training))
            for training in await uow.trainings.list_between(
                now - FIX_HORIZON, now, squad_id=squad_id
            ):
                if await self._counted(uow, training):
                    prompts.append(await self._past_prompt(uow, training))
            if not prompts:
                settings = await load_squad_settings(uow, squad_id)
                hours = f"{settings.duty_lead_hours:g}"
                prompts.append(Prompt(ru.DUTY_MANAGE_EMPTY.format(hours=hours)))
            return prompts

    async def confirm_replace(
        self, actor: UserContext, assignment_id: int, *, now: datetime | None = None
    ) -> Prompt:
        now = now or utcnow()
        async with self._uow_factory() as uow:
            assignment, training = await self._managed_assignment(uow, actor, assignment_id, now)
            old_user = await uow.users.get(assignment.user_id)
            next_user = await self._next_in_queue(uow, training, assignment, now)
            when = await self._when(uow, training)
        next_line = (
            ru.DUTY_CONFIRM_NEXT.format(name=html.escape(to_dto(next_user).display_name))
            if next_user
            else ru.DUTY_CONFIRM_NO_NEXT
        )
        return Prompt(
            ru.DUTY_CONFIRM_REPLACE.format(
                old=html.escape(to_dto(old_user).display_name) if old_user else "?",
                when=when,
                next=next_line,
            ),
            _confirm_keyboard(
                ru.DUTY_BTN_CONFIRM_REPLACE, callbacks.DUTY_REPLACE_OK, assignment_id
            ),
        )

    async def replace(
        self, actor: UserContext, assignment_id: int, *, now: datetime | None = None
    ) -> str:
        """Замена следующим по очереди. Если заменить некем — капитану список для выбора."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            assignment, training = await self._managed_assignment(
                uow, actor, assignment_id, now, lock=True
            )
            new_user, notified = await self._replace(
                uow, training, assignment, now, by_user_id=actor.user.id
            )
            if new_user is None:
                await self._send_pick_list(uow, actor.user.id, training, ru.DUTY_NO_REPLACEMENT)
                await uow.commit()
                return ru.DUTY_MANUAL_SENT
            await uow.commit()
            return _picked(new_user, notified)

    async def send_pick_list(
        self, actor: UserContext, training_id: int, *, now: datetime | None = None
    ) -> bool:
        now = now or utcnow()
        async with self._uow_factory() as uow:
            training = await self._get_upcoming(uow, training_id, now)
            self._require_manager(actor, training.squad_id)
            delivered = await self._send_pick_list(
                uow, actor.user.id, training, ru.DUTY_PICK_PROMPT
            )
            await uow.commit()
            return delivered

    async def confirm_pick(
        self, actor: UserContext, training_id: int, user_id: int, *, now: datetime | None = None
    ) -> Prompt:
        now = now or utcnow()
        async with self._uow_factory() as uow:
            training = await self._get_upcoming(uow, training_id, now)
            self._require_manager(actor, training.squad_id)
            user = await self._pickable_user(uow, training, user_id)
            when = await self._when(uow, training)
        return Prompt(
            ru.DUTY_CONFIRM_PICK.format(name=html.escape(to_dto(user).display_name), when=when),
            _confirm_keyboard(
                ru.DUTY_BTN_CONFIRM_PICK, callbacks.DUTY_PICK_OK, training_id, user_id
            ),
        )

    async def manual_pick(
        self, actor: UserContext, training_id: int, user_id: int, *, now: datetime | None = None
    ) -> str:
        now = now or utcnow()
        async with self._uow_factory() as uow:
            training = await self._get_upcoming(uow, training_id, now, lock=True)
            self._require_manager(actor, training.squad_id)
            user = await self._pickable_user(uow, training, user_id)
            assignment = await uow.trainings.add_duty(
                DutyAssignment(
                    training_id=training_id, user_id=user_id, assigned_by_user_id=actor.user.id
                )
            )
            settings = await load_squad_settings(uow, training.squad_id)
            await self._refresh_announcement(uow, training, settings)
            posted = await self._post_to_group(
                uow,
                training,
                ru.DUTY_PICKED_GROUP.format(
                    when=await self._when(uow, training),
                    new=to_dto(user).mention_html,
                    minutes=settings.duty_arrive_minutes,
                ),
            )
            delivered = await self._notify_assigned(uow, training, assignment, settings)
            await uow.commit()
            return _picked(user, posted or delivered)

    # --- исправление прошедшего дежурства ---
    async def fix_pick_list(
        self, actor: UserContext, assignment_id: int, *, now: datetime | None = None
    ) -> Prompt:
        """Список участников: кто дежурил на самом деле вместо назначенного."""
        async with self._uow_factory() as uow:
            assignment, training = await self._fixable_assignment(
                uow, actor, assignment_id, now or utcnow()
            )
            old_user = await uow.users.get(assignment.user_id)
            counted = {a.user_id for a in await self._counted(uow, training)}
            keyboard = await self._members_keyboard(
                uow,
                training,
                counted,
                lambda user: _button(
                    to_dto(user).display_name, callbacks.DUTY_FIX_PICK, assignment_id, user.id
                ),
            )
            return Prompt(
                ru.DUTY_FIX_PICK_PROMPT.format(
                    when=await self._when(uow, training),
                    old=html.escape(to_dto(old_user).display_name) if old_user else "?",
                ),
                keyboard,
            )

    async def confirm_fix(
        self, actor: UserContext, assignment_id: int, user_id: int, *, now: datetime | None = None
    ) -> Prompt:
        async with self._uow_factory() as uow:
            assignment, training = await self._fixable_assignment(
                uow, actor, assignment_id, now or utcnow()
            )
            new_user = await self._fix_candidate(uow, training, user_id)
            old_user = await uow.users.get(assignment.user_id)
            when = await self._when(uow, training)
        return Prompt(
            ru.DUTY_CONFIRM_FIX.format(
                when=when,
                new=html.escape(to_dto(new_user).display_name),
                old=html.escape(to_dto(old_user).display_name) if old_user else "?",
            ),
            _confirm_keyboard(
                ru.DUTY_BTN_CONFIRM_FIX, callbacks.DUTY_FIX_OK, assignment_id, user_id
            ),
        )

    async def fix(
        self, actor: UserContext, assignment_id: int, user_id: int, *, now: datetime | None = None
    ) -> str:
        """Дежурство засчитывается фактически дежурившему, назначенному — нет. Без уведомлений:
        тренировка уже прошла, объявление в группе не трогаем."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            assignment, training = await self._fixable_assignment(
                uow, actor, assignment_id, now, lock=True
            )
            new_user = await self._fix_candidate(uow, training, user_id)
            old_user_id = assignment.user_id
            assignment.status = DutyStatus.REPLACED
            assignment.replaced_at = now
            await uow.trainings.add_duty(
                DutyAssignment(
                    training_id=training.id,
                    user_id=user_id,
                    status=DutyStatus.DONE,
                    assigned_by_user_id=actor.user.id,
                )
            )
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="duty.fix",
                entity="training",
                entity_id=training.id,
                payload={"from": old_user_id, "to": user_id},
            )
            await uow.commit()
            log.info("duty_fixed", training_id=training.id, old=old_user_id, new=user_id)
        return ru.DUTY_FIXED.format(name=html.escape(to_dto(new_user).display_name))

    async def _fixable_assignment(
        self,
        uow: UnitOfWork,
        actor: UserContext,
        assignment_id: int,
        now: datetime,
        *,
        lock: bool = False,
    ) -> tuple[DutyAssignment, Training]:
        assignment, training = await self._load_assignment(uow, assignment_id, lock=lock)
        if assignment.status not in COUNTED_DUTY_STATUSES:
            raise Conflict(ru.DUTY_ALREADY_CHANGED)
        self._require_manager(actor, training.squad_id)
        if (
            training.status not in ACTIVE_TRAINING_STATUSES
            or not now - FIX_HORIZON <= training.starts_at <= now
        ):
            raise Conflict(ru.DUTY_FIX_UNAVAILABLE.format(days=FIX_HORIZON.days))
        return assignment, training

    async def _fix_candidate(self, uow: UnitOfWork, training: Training, user_id: int) -> User:
        if user_id in {a.user_id for a in await self._counted(uow, training)}:
            raise Conflict(ru.DUTY_ALREADY_ASSIGNED)
        return await self._active_member(uow, training.squad_id, user_id)

    # --- проверки ---
    @staticmethod
    def _require_manager(actor: UserContext, squad_id: int) -> None:
        if not actor.has_role(squad_id, *DUTY_MANAGERS):
            raise PermissionDenied(ru.NO_PERMISSION)

    @staticmethod
    async def _get_upcoming(
        uow: UnitOfWork, training_id: int, now: datetime, *, lock: bool = False
    ) -> Training:
        training = await uow.trainings.get(training_id, for_update=lock)
        if (
            training is None
            or training.status not in ACTIVE_TRAINING_STATUSES
            or training.starts_at <= now
        ):
            raise Conflict(ru.DUTY_TRAINING_PASSED)
        return training

    @staticmethod
    async def _load_assignment(
        uow: UnitOfWork, assignment_id: int, *, lock: bool
    ) -> tuple[DutyAssignment, Training]:
        """Назначение и его тренировка. lock — тренировка блокируется, а назначение после этого
        перечитывается: параллельная операция могла уже изменить его статус."""
        assignment = await uow.trainings.get_duty(assignment_id)
        if assignment is None:
            raise Conflict(ru.DUTY_ALREADY_CHANGED)
        training = await uow.trainings.get(assignment.training_id, for_update=lock)
        if lock:
            assignment = await uow.trainings.get_duty(assignment_id, refresh=True)
        if assignment is None or training is None:
            raise Conflict(ru.DUTY_ALREADY_CHANGED)
        return assignment, training

    async def _active_assignment(
        self, uow: UnitOfWork, assignment_id: int, now: datetime, *, lock: bool
    ) -> tuple[DutyAssignment, Training]:
        assignment, training = await self._load_assignment(uow, assignment_id, lock=lock)
        if assignment.status != DutyStatus.ASSIGNED:
            raise Conflict(ru.DUTY_ALREADY_CHANGED)
        if training.status not in ACTIVE_TRAINING_STATUSES or training.starts_at <= now:
            raise Conflict(ru.DUTY_TRAINING_PASSED)
        return assignment, training

    async def _managed_assignment(
        self,
        uow: UnitOfWork,
        actor: UserContext,
        assignment_id: int,
        now: datetime,
        *,
        lock: bool = False,
    ) -> tuple[DutyAssignment, Training]:
        assignment, training = await self._active_assignment(uow, assignment_id, now, lock=lock)
        self._require_manager(actor, training.squad_id)
        return assignment, training

    async def _own_assignment(
        self,
        uow: UnitOfWork,
        actor: UserContext,
        assignment_id: int,
        now: datetime,
        *,
        lock: bool = False,
    ) -> tuple[DutyAssignment, Training]:
        assignment, training = await self._active_assignment(uow, assignment_id, now, lock=lock)
        if assignment.user_id != actor.user.id:
            raise PermissionDenied(ru.DUTY_NOT_YOURS)
        return assignment, training

    @staticmethod
    async def _pickable_user(uow: UnitOfWork, training: Training, user_id: int) -> User:
        if await uow.trainings.active_duty_for_user(training.id, user_id):
            raise Conflict(ru.DUTY_ALREADY_ASSIGNED)
        return await DutyService._active_member(uow, training.squad_id, user_id)

    @staticmethod
    async def _active_member(uow: UnitOfWork, squad_id: int, user_id: int) -> User:
        member = await uow.members.get(user_id, squad_id)
        user = await uow.users.get(user_id)
        if member is None or user is None or member.status != MembershipStatus.ACTIVE:
            raise NotFound(ru.MEMBER_NOT_FOUND)
        return user

    # --- замена ---
    async def _next_in_queue(
        self, uow: UnitOfWork, training: Training, assignment: DutyAssignment, now: datetime
    ) -> User | None:
        """Следующий по очереди на место assignment. Детерминирован для пары тренировка/назначение,
        поэтому вопрос «заменить на Петрова?» и сама замена выбирают одного и того же человека."""
        settings = await load_squad_settings(uow, training.squad_id)
        already = {a.user_id for a in await uow.trainings.list_duties(training.id)}
        chosen = select_duty(
            await self._candidates(uow, training),
            count=1,
            now=now,
            grace_days=settings.duty_newcomer_grace_days,
            exclude=already,
            rng=random.Random(f"{training.id}:{assignment.id}"),
        )
        return await uow.users.get(chosen[0]) if chosen else None

    async def _replace(
        self,
        uow: UnitOfWork,
        training: Training,
        assignment: DutyAssignment,
        now: datetime,
        *,
        by_user_id: int | None,
    ) -> tuple[User | None, bool]:
        """Снимает дежурного (назначение не засчитывается) и назначает следующего по очереди.

        В группу — новое сообщение о замене (правка объявления уведомлений не присылает).
        Возвращает нового дежурного и получил ли он уведомление (в личку или упоминанием)."""
        new_user = await self._next_in_queue(uow, training, assignment, now)
        assignment.status = DutyStatus.REPLACED
        assignment.replaced_at = now
        settings = await load_squad_settings(uow, training.squad_id)
        old_user = await uow.users.get(assignment.user_id)
        old_name = html.escape(to_dto(old_user).display_name) if old_user else "?"
        when = await self._when(uow, training)
        new_assignment = None
        if new_user is not None:
            new_assignment = await uow.trainings.add_duty(
                DutyAssignment(
                    training_id=training.id, user_id=new_user.id, assigned_by_user_id=by_user_id
                )
            )
        await self._refresh_announcement(uow, training, settings)
        notified = False
        if new_user is not None and new_assignment is not None:
            posted = await self._post_to_group(
                uow,
                training,
                ru.DUTY_REPLACED_GROUP.format(
                    when=when,
                    old=old_name,
                    new=to_dto(new_user).mention_html,
                    minutes=settings.duty_arrive_minutes,
                ),
            )
            delivered = await self._notify_assigned(
                uow, training, new_assignment, settings, instead=old_user
            )
            notified = posted or delivered
        else:
            await self._post_to_group(
                uow, training, ru.DUTY_REMOVED_GROUP.format(when=when, old=old_name)
            )
        # снятого капитаном/тренером предупреждаем; кто отказался сам — и так знает
        if by_user_id is not None and old_user is not None and by_user_id != old_user.id:
            replacement = to_dto(new_user).display_name if new_user else "—"
            await send_direct(
                self._notifier,
                old_user,
                ru.DUTY_UNASSIGNED_DM.format(
                    when=await self._when(uow, training), name=html.escape(replacement)
                ),
            )
        return new_user, notified

    async def _replace_and_report(
        self,
        uow: UnitOfWork,
        training: Training,
        assignment: DutyAssignment,
        now: datetime,
        template: str,
    ) -> None:
        """Замена после отказа дежурного: капитану — кто кого заменил или список для выбора."""
        old_user = await uow.users.get(assignment.user_id)
        new_user, notified = await self._replace(uow, training, assignment, now, by_user_id=None)
        if new_user is not None:
            new_name = html.escape(to_dto(new_user).display_name)
            text = template.format(
                old=html.escape(to_dto(old_user).display_name) if old_user else "?",
                when=await self._when(uow, training),
                new=new_name,
            )
            if not notified:
                text += ru.DUTY_NOT_NOTIFIED_CAPTAIN.format(name=new_name)
            await notify_staff(
                uow, self._notifier, training.squad_id, text, roles=[SquadRole.CAPTAIN]
            )
            return
        settings = await load_squad_settings(uow, training.squad_id)
        await self._notify_shortage(
            uow, training, len(await self._active(uow, training)), settings.duty_count
        )

    # --- игрок вышел из группы ---
    async def handle_member_left(
        self, user_id: int, squad_id: int, *, now: datetime | None = None
    ) -> int:
        """Вышедший из группы снимается с будущих дежурств состава: автозамена, капитану —
        уведомление (если заменить некем — список для выбора). Возвращает число замен."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            pending = [
                (a.id, a.training_id)
                for a in await uow.trainings.upcoming_duties_for_user(user_id, squad_id, now)
            ]
        replaced = 0
        for assignment_id, training_id in pending:
            async with self._uow_factory() as uow:
                training = await uow.trainings.get(training_id, for_update=True)
                assignment = await uow.trainings.get_duty(assignment_id, refresh=True)
                if (
                    training is None
                    or assignment is None
                    or assignment.status != DutyStatus.ASSIGNED
                    or training.status not in ACTIVE_TRAINING_STATUSES
                    or training.starts_at <= now
                ):
                    continue
                await self._replace_and_report(uow, training, assignment, now, ru.DUTY_LEFT_CAPTAIN)
                await uow.commit()
                replaced += 1
        return replaced

    # --- сообщения ---
    async def _when(self, uow: UnitOfWork, training: Training) -> str:
        return format_local(training.starts_at, squad_tz(await uow.squads.get(training.squad_id)))

    @staticmethod
    async def _active(uow: UnitOfWork, training: Training) -> list[DutyAssignment]:
        return [
            a
            for a in await uow.trainings.list_duties(training.id)
            if a.status == DutyStatus.ASSIGNED
        ]

    @staticmethod
    async def _counted(uow: UnitOfWork, training: Training) -> list[DutyAssignment]:
        """Засчитываемые назначения тренировки (assigned/done)."""
        return [
            a
            for a in await uow.trainings.list_duties(training.id)
            if a.status in COUNTED_DUTY_STATUSES
        ]

    async def _announcement_text(
        self, uow: UnitOfWork, training: Training, settings: SquadSettings
    ) -> str:
        active = await self._active(uow, training)
        users = {u.id: u for u in await uow.users.list_by_ids([a.user_id for a in active])}
        names = "\n".join(f"• {to_dto(users[a.user_id]).mention_html}" for a in active)
        return ru.DUTY_ANNOUNCE.format(
            when=await self._when(uow, training),
            names=names or ru.DUTY_NOBODY,
            minutes=settings.duty_arrive_minutes,
        )

    async def _publish_announcement(
        self, uow: UnitOfWork, training: Training, settings: SquadSettings
    ) -> bool:
        """Объявление о дежурных в топике «Тренировки». True — опубликовано."""
        destination = await resolve_destination(uow, training.squad_id, TopicPurpose.TRAININGS)
        if destination is None:
            return False
        message_id = await self._notifier.send_message(
            destination.chat_id,
            await self._announcement_text(uow, training, settings),
            thread_id=destination.thread_id,
        )
        if message_id is None:
            return False
        training.duty_chat_id, training.duty_message_id = destination.chat_id, message_id
        return True

    async def _post_to_group(self, uow: UnitOfWork, training: Training, text: str) -> bool:
        """Новое сообщение об изменении дежурных — ответом на объявление, чтобы было видно, к
        чему оно относится. True — доставлено."""
        destination = await resolve_destination(uow, training.squad_id, TopicPurpose.TRAININGS)
        if destination is None:
            return False
        message_id = await self._notifier.send_message(
            destination.chat_id,
            text,
            thread_id=destination.thread_id,
            reply_to=training.duty_message_id,
        )
        return message_id is not None

    async def _refresh_announcement(
        self, uow: UnitOfWork, training: Training, settings: SquadSettings
    ) -> None:
        if training.duty_chat_id is None or training.duty_message_id is None:
            return
        await self._notifier.edit_message(
            training.duty_chat_id,
            training.duty_message_id,
            await self._announcement_text(uow, training, settings),
        )

    async def _notify_assigned(
        self,
        uow: UnitOfWork,
        training: Training,
        assignment: DutyAssignment,
        settings: SquadSettings,
        *,
        instead: User | None = None,
    ) -> bool:
        """Личное сообщение дежурному с кнопкой «Не могу дежурить». True — доставлено."""
        user = await uow.users.get(assignment.user_id)
        if user is None:
            return False
        when = await self._when(uow, training)
        instead_text = (
            ru.DUTY_INSTEAD.format(name=html.escape(to_dto(instead).display_name))
            if instead
            else ""
        )
        return await send_direct(
            self._notifier,
            user,
            ru.DUTY_ASSIGNED_DM.format(
                when=when,
                venue=html.escape(training.venue),
                instead=instead_text,
                minutes=settings.duty_arrive_minutes,
            ),
            keyboard=[[_button(ru.DUTY_BTN_DECLINE, callbacks.DUTY_DECLINE, assignment.id)]],
        )

    async def _staff_prompt(
        self, uow: UnitOfWork, training: Training, not_notified: Collection[int] = ()
    ) -> Prompt:
        active = await self._active(uow, training)
        users = {u.id: u for u in await uow.users.list_by_ids([a.user_id for a in active])}
        squad = await uow.squads.get(training.squad_id)
        names = "\n".join(
            f"• {html.escape(to_dto(users[a.user_id]).display_name)}"
            + (ru.DUTY_NOT_NOTIFIED_MARK if a.user_id in not_notified else "")
            for a in active
        )
        keyboard: list[list[InlineButton]] = [
            [
                _button(
                    ru.DUTY_BTN_REPLACE.format(name=to_dto(users[a.user_id]).display_name),
                    callbacks.DUTY_REPLACE,
                    a.id,
                )
            ]
            for a in active
        ]
        keyboard.append([_button(ru.DUTY_BTN_MANUAL, callbacks.DUTY_MANUAL, training.id)])
        return Prompt(
            ru.DUTY_STAFF_DM.format(
                when=await self._when(uow, training),
                squad=html.escape(squad.name) if squad else "",
                names=names or ru.DUTY_NOBODY,
            ),
            keyboard,
        )

    async def _past_prompt(self, uow: UnitOfWork, training: Training) -> Prompt:
        counted = await self._counted(uow, training)
        users = {u.id: u for u in await uow.users.list_by_ids([a.user_id for a in counted])}
        squad = await uow.squads.get(training.squad_id)
        names = "\n".join(
            f"• {html.escape(to_dto(users[a.user_id]).display_name)}" for a in counted
        )
        keyboard = [
            [
                _button(
                    ru.DUTY_BTN_FIX.format(name=to_dto(users[a.user_id]).display_name),
                    callbacks.DUTY_FIX,
                    a.id,
                )
            ]
            for a in counted
        ]
        return Prompt(
            ru.DUTY_PAST_DM.format(
                when=await self._when(uow, training),
                squad=html.escape(squad.name) if squad else "",
                names=names,
            ),
            keyboard,
        )

    async def _send_staff_summary(
        self, uow: UnitOfWork, training: Training, not_notified: Collection[int] = ()
    ) -> None:
        """Капитану (нет капитана — тренеру) в личку: кто дежурит и кнопки управления."""
        prompt = await self._staff_prompt(uow, training, not_notified)
        for roles in ([SquadRole.CAPTAIN], [SquadRole.COACH]):
            if await notify_staff(
                uow,
                self._notifier,
                training.squad_id,
                prompt.text,
                roles=roles,
                keyboard=prompt.keyboard,
            ):
                return

    async def _notify_shortage(
        self, uow: UnitOfWork, training: Training, assigned: int, need: int
    ) -> None:
        """Не хватает дежурных: список для ручного выбора капитану (если капитана нет — тренеру)."""
        squad = await uow.squads.get(training.squad_id)
        text = ru.DUTY_SHORTAGE_CAPTAIN.format(
            when=await self._when(uow, training),
            squad=html.escape(squad.name) if squad else "",
            assigned=assigned,
            need=need,
        )
        keyboard = await self._pick_keyboard(uow, training)
        for roles in ([SquadRole.CAPTAIN], [SquadRole.COACH]):
            if await notify_staff(
                uow, self._notifier, training.squad_id, text, roles=roles, keyboard=keyboard
            ):
                return

    async def _pick_keyboard(self, uow: UnitOfWork, training: Training) -> Keyboard:
        assigned = {a.user_id for a in await self._active(uow, training)}
        return await self._members_keyboard(
            uow,
            training,
            assigned,
            lambda user: _button(
                to_dto(user).display_name, callbacks.DUTY_PICK, training.id, user.id
            ),
        )

    @staticmethod
    async def _members_keyboard(
        uow: UnitOfWork,
        training: Training,
        exclude: Collection[int],
        button: Callable[[User], InlineButton],
    ) -> Keyboard:
        """Активные участники состава кнопками по два в ряд (кроме exclude)."""
        buttons = [
            button(user)
            for _, user in await uow.members.list_active_for_squad(training.squad_id)
            if user.id not in exclude
        ][:MAX_PICK_BUTTONS]
        return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

    async def _send_pick_list(
        self, uow: UnitOfWork, to_user_id: int, training: Training, intro: str
    ) -> bool:
        user = await uow.users.get(to_user_id)
        if user is None:
            return False
        when = await self._when(uow, training)
        text = f"{intro}\nТренировка {when}, {html.escape(training.venue)}"
        return await send_direct(
            self._notifier, user, text, keyboard=await self._pick_keyboard(uow, training)
        )

    @staticmethod
    async def _candidates(uow: UnitOfWork, training: Training) -> list[DutyCandidate]:
        """Активные участники состава, ответившие «Да» на опрос этой тренировки."""
        poll = await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training.id)
        if poll is None:
            return []
        yes_ids = {
            user_id
            for user_id, options in (await uow.polls.answers(poll.id)).items()
            if TRAINING_POLL_YES in options
        }
        members = [
            m
            for m, _ in await uow.members.list_active_for_squad(training.squad_id)
            if m.user_id in yes_ids
        ]
        last = await uow.trainings.last_duty_at(training.squad_id, [m.user_id for m in members])
        return [
            DutyCandidate(
                user_id=m.user_id,
                first_joined_at=m.first_joined_at,
                last_duty_at=last.get(m.user_id),
            )
            for m in members
        ]
