"""Опросы: утренний опрос на тренировку (ФТ-2), приём ответов, сводка посещаемости."""

import html
from datetime import datetime, timedelta

from pfbc_bot.db.models import Poll
from pfbc_bot.domain.dates import format_local, training_poll_at, utcnow
from pfbc_bot.domain.enums import (
    ACTIVE_TRAINING_STATUSES,
    TRAINING_POLL_OPTIONS,
    TRAINING_POLL_YES,
    MembershipStatus,
    PollType,
    SquadRole,
    TopicPurpose,
)
from pfbc_bot.logging import get_logger
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.schemas.trainings import AttendanceSummary, TrainingDTO
from pfbc_bot.schemas.users import TelegramUser
from pfbc_bot.services.common import resolve_destination, squad_tz, to_dto
from pfbc_bot.services.duty import DutyService
from pfbc_bot.services.errors import NotFound, PermissionDenied
from pfbc_bot.services.membership import MembershipService
from pfbc_bot.services.ports import Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)
TRAINING_REF = "training"


def poll_key(training_id: int) -> str:
    """Ключ reminder_log опроса тренировки (снимается при переносе — опрос публикуется заново)."""
    return f"poll:{training_id}"


class PollService:
    def __init__(
        self,
        uow_factory: UowFactory,
        notifier: Notifier,
        duty: DutyService,
        membership: MembershipService,
    ) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier
        self._duty = duty
        self._membership = membership

    async def publish_training_polls(self, *, now: datetime | None = None) -> int:
        """Публикует опрос в день тренировки в training_poll_time (по времени состава), а на
        раннюю тренировку — накануне в training_poll_eve_time.

        Отметка poll:<id> фиксируется до отправки, поэтому сбой не плодит опросы. Если Telegram
        опрос не принял, отметка снимается и следующий тик пробует снова."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            training_ids = [
                t.id for t in await uow.trainings.list_between(now, now + timedelta(days=1))
            ]
        published = 0
        for training_id in training_ids:
            async with self._uow_factory() as uow:
                training = await uow.trainings.get(training_id)
                if training is None or training.status not in ACTIVE_TRAINING_STATUSES:
                    continue
                settings = await load_squad_settings(uow, training.squad_id)
                if not settings.features.training_polls:
                    continue
                tz = squad_tz(await uow.squads.get(training.squad_id))
                poll_at = training_poll_at(
                    starts_at=training.starts_at,
                    tz=tz,
                    poll_time=settings.training_poll_time,
                    eve_time=settings.training_poll_eve_time,
                    duty_lead=timedelta(hours=settings.duty_lead_hours),
                    early_until=settings.early_training_until,
                )
                if now < poll_at:
                    continue
                if await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training.id):
                    continue
                destination = await resolve_destination(
                    uow, training.squad_id, TopicPurpose.TRAININGS
                )
                if destination is None:
                    continue
                key = poll_key(training.id)
                if not await uow.system.claim_reminder(key, "training_poll"):
                    continue
                await uow.commit()
                question = ru.TRAINING_POLL_QUESTION.format(
                    when=format_local(training.starts_at, tz), venue=training.venue
                )
                sent = await self._notifier.send_poll(
                    destination.chat_id,
                    question,
                    TRAINING_POLL_OPTIONS,
                    thread_id=destination.thread_id,
                )
                if sent is None:
                    await uow.system.release_reminder(key)  # повторим на следующем тике
                    await uow.commit()
                    continue
                await uow.polls.add(
                    Poll(
                        type=PollType.TRAINING,
                        squad_id=training.squad_id,
                        ref_type=TRAINING_REF,
                        ref_id=training.id,
                        tg_poll_id=sent.poll_id,
                        chat_id=destination.chat_id,
                        message_id=sent.message_id,
                        question=question[:300],
                        options=list(TRAINING_POLL_OPTIONS),
                        closes_at=training.starts_at,
                    )
                )
                await uow.commit()
                published += 1
        return published

    async def record_answer(
        self,
        tg_poll_id: str,
        tg_user_id: int,
        option_ids: list[int],
        *,
        profile: TelegramUser | None = None,
        now: datetime | None = None,
    ) -> None:
        """Сохраняет ответ участника состава. Если бот не знал о его членстве (участник был в
        группе до подключения бота), — проверяет в Telegram и заводит; ответы не участников
        не учитываются (ФТ-0). profile — имя и username из Telegram для новой записи."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            poll = await uow.polls.get_by_tg_id(tg_poll_id)
            if poll is None:
                return
            chat_id, squad_id = poll.chat_id, poll.squad_id
            known = await self._is_active_member(uow, tg_user_id, squad_id)
        if not known and not await self._membership.verify_in_chat(
            chat_id, profile or TelegramUser(tg_user_id=tg_user_id), now=now
        ):
            return

        async with self._uow_factory() as uow:
            poll = await uow.polls.get_by_tg_id(tg_poll_id)
            user = await uow.users.get_by_tg_id(tg_user_id)
            if poll is None or user is None:
                return
            if not await self._is_active_member(uow, tg_user_id, poll.squad_id):
                return
            await uow.polls.upsert_answer(poll.id, user.id, option_ids, now)
            await uow.commit()
            poll_type, ref_id, user_id = poll.type, poll.ref_id, user.id
            superseded = poll.superseded_at is not None

        declined = bool(option_ids) and TRAINING_POLL_YES not in option_ids
        # ответ в устаревшем опросе (тренировку перенесли) на дежурства не влияет
        if poll_type == PollType.TRAINING and ref_id is not None and declined and not superseded:
            await self._duty.handle_decline(ref_id, user_id, now=now)

    @staticmethod
    async def _is_active_member(uow: UnitOfWork, tg_user_id: int, squad_id: int) -> bool:
        user = await uow.users.get_by_tg_id(tg_user_id)
        if user is None:
            return False
        member = await uow.members.get(user.id, squad_id)
        return member is not None and member.status == MembershipStatus.ACTIVE

    async def attendance(self, actor: UserContext, training_id: int) -> AttendanceSummary:
        async with self._uow_factory() as uow:
            training = await uow.trainings.get(training_id)
            if training is None:
                raise NotFound("Тренировка не найдена")
            if not actor.has_role(training.squad_id, SquadRole.COACH, SquadRole.CAPTAIN):
                raise PermissionDenied(ru.NO_PERMISSION)
            poll = await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training.id)
            if poll is None:
                raise NotFound(ru.ATTENDANCE_NO_POLL)
            answers = await uow.polls.answers(poll.id)
            members = await uow.members.list_active_for_squad(training.squad_id)
            options = [str(o) for o in poll.options]
            by_option: dict[str, list[str]] = {o: [] for o in options}
            not_answered: list[str] = []
            for _, user in members:
                name = html.escape(to_dto(user).display_name)
                chosen = answers.get(user.id)
                if not chosen:
                    not_answered.append(name)
                    continue
                for idx in chosen:
                    if 0 <= idx < len(options):
                        by_option[options[idx]].append(name)
            return AttendanceSummary(
                training=TrainingDTO.model_validate(training),
                by_option=by_option,
                not_answered=not_answered,
            )
