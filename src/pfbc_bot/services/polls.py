"""Опросы: утренний опрос на тренировку (ФТ-2), ручной запуск, приём ответов, сводка."""

from datetime import datetime, timedelta

from pfbc_bot.db.models import Poll, Training
from pfbc_bot.domain.dates import format_local, training_poll_at, utcnow
from pfbc_bot.domain.enums import (
    ACTIVE_TRAINING_STATUSES,
    TRAINING_POLL_OPTIONS,
    TRAINING_POLL_YES,
    MembershipStatus,
    PollType,
    TopicPurpose,
)
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.logging import get_logger
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.squads import Destination, UserContext
from pfbc_bot.schemas.trainings import AttendanceSummary, EventPollSummary, TrainingDTO
from pfbc_bot.schemas.users import TelegramUser
from pfbc_bot.services.common import resolve_destination, squad_tz, to_dto
from pfbc_bot.services.duty import DutyService
from pfbc_bot.services.errors import Conflict, NotFound
from pfbc_bot.services.events import EVENT_POLLS, EVENT_REF, to_dtos
from pfbc_bot.services.game_payments import GamePaymentService
from pfbc_bot.services.membership import MembershipService
from pfbc_bot.services.permissions import require
from pfbc_bot.services.ports import Notifier, SentPoll
from pfbc_bot.services.receipts import ReceiptService
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)
TRAINING_REF = "training"


def poll_key(training_id: int) -> str:
    """Ключ reminder_log опроса тренировки (снимается при переносе — опрос публикуется заново)."""
    return f"poll:{training_id}"


def _training_poll(
    training: Training, destination: Destination, question: str, sent: SentPoll
) -> Poll:
    return Poll(
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


class PollService:
    def __init__(
        self,
        uow_factory: UowFactory,
        notifier: Notifier,
        duty: DutyService,
        membership: MembershipService,
        game_payments: GamePaymentService,
        receipts: ReceiptService,
    ) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier
        self._duty = duty
        self._membership = membership
        self._game_payments = game_payments
        self._receipts = receipts

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
                sent = await self._send_poll(destination, question)
                if sent is None:
                    await uow.system.release_reminder(key)  # повторим на следующем тике
                    await uow.commit()
                    continue
                # по очереди с ручным запуском: если, пока отправляли, опрос уже запустили
                # вручную, — действует он, а наш закрывается
                await uow.trainings.get(training_id, for_update=True)
                if await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training_id):
                    await self._notifier.stop_poll(destination.chat_id, sent.message_id)
                    continue
                await uow.polls.add(_training_poll(training, destination, question, sent))
                await uow.commit()
                published += 1
        return published

    async def start_training_poll(
        self, actor: UserContext, training_id: int, *, now: datetime | None = None
    ) -> None:
        """Опрос на тренировку по команде — раньше планировщика или заново. Действующий опрос
        закрывается (ответы в нём больше ни на что не влияют), отметка poll:<id> занимается —
        планировщик на эту тренировку опрос уже не публикует.

        В две фазы, чтобы отправка в Telegram не держала блокировку тренировки: проверки и
        отметка → отправка → под блокировкой запись нового опроса и закрытие действующего
        (действующим мог стать и опрос планировщика, отправленный параллельно)."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            training = await uow.trainings.get(training_id, for_update=True)
            if training is None or training.status not in ACTIVE_TRAINING_STATUSES:
                raise NotFound("Тренировка не найдена или уже отменена")
            require(actor, training.squad_id, Permission.TRAINING_POLL)
            if training.starts_at <= now:
                raise Conflict(ru.POLL_TRAINING_STARTED)
            destination = await resolve_destination(uow, training.squad_id, TopicPurpose.TRAININGS)
            if destination is None:
                raise Conflict(ru.POLL_NO_CHAT)
            had_poll = (
                await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training.id)
                is not None
            )
            claimed = await uow.system.claim_reminder(poll_key(training.id), "training_poll")
            tz = squad_tz(await uow.squads.get(training.squad_id))
            question = ru.TRAINING_POLL_QUESTION.format(
                when=format_local(training.starts_at, tz), venue=training.venue
            )
            await uow.commit()

        sent = await self._send_poll(destination, question)
        if sent is None:  # прежний опрос остаётся действующим; отметку — вернуть планировщику
            if claimed and not had_poll:
                async with self._uow_factory() as uow:
                    await uow.system.release_reminder(poll_key(training_id))
                    await uow.commit()
            raise Conflict(ru.POLL_NOT_SENT)

        async with self._uow_factory() as uow:
            training = await uow.trainings.get(training_id, for_update=True)
            if training is None or training.status not in ACTIVE_TRAINING_STATUSES:
                # тренировку отменили, пока отправляли, — наш опрос не нужен
                await uow.rollback()
                await self._notifier.stop_poll(destination.chat_id, sent.message_id)
                raise NotFound("Тренировка не найдена или уже отменена")
            previous = await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training.id)
            if previous is not None:
                await uow.polls.supersede(previous, now)
            await uow.polls.add(_training_poll(training, destination, question, sent))
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="poll.start" if previous is None else "poll.restart",
                entity="training",
                entity_id=training.id,
            )
            await uow.commit()
        if previous is not None:
            await self._notifier.stop_poll(previous.chat_id, previous.message_id)

    async def close_due(self, *, now: datetime | None = None) -> int:
        """Закрывает голосование, когда тренировка или событие началось: отвечать «приду»
        после начала незачем. Ответы сохраняются и видны в сводках."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            polls = await uow.polls.list_to_close(now)
            for poll in polls:
                poll.closed_at = now
            await uow.commit()
        for poll in polls:
            await self._notifier.stop_poll(poll.chat_id, poll.message_id)
        return len(polls)

    async def mark_closed(self, tg_poll_id: str, *, now: datetime | None = None) -> bool:
        """Telegram сообщил, что опрос закрыт: отмечаем — по сбору денег или квитанций
        прекращаются напоминания. True — опрос был открыт."""
        async with self._uow_factory() as uow:
            poll = await uow.polls.get_by_tg_id(tg_poll_id)
            if poll is None or poll.closed_at is not None:
                return False
            poll.closed_at = now or utcnow()
            await uow.commit()
            log.info("poll_closed_in_telegram", poll_id=poll.id, type=poll.type)
            return True

    async def trainings_with_polls(self, training_ids: list[int]) -> set[int]:
        """Тренировки, по которым опрос уже опубликован (для списка в /attendance)."""
        async with self._uow_factory() as uow:
            return await uow.polls.refs_with_polls(PollType.TRAINING, TRAINING_REF, training_ids)

    async def has_training_poll(self, training_id: int) -> bool:
        async with self._uow_factory() as uow:
            poll = await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training_id)
            return poll is not None

    async def _send_poll(self, destination: Destination, question: str) -> SentPoll | None:
        return await self._notifier.send_poll(
            destination.chat_id,
            question,
            TRAINING_POLL_OPTIONS,
            thread_id=destination.thread_id,
        )

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

        # все отметились: «Оплатил» — все игроки матча, «Отправил» — все, кроме тренеров
        if ref_id is not None and not superseded:
            if poll_type == PollType.GAME_PAYMENT:
                await self._game_payments.notify_if_all_paid(ref_id, now=now)
            elif poll_type == PollType.RECEIPT:
                await self._receipts.notify_if_all_sent(ref_id)

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
            require(actor, training.squad_id, Permission.ATTENDANCE)
            poll = await uow.polls.get_for_ref(PollType.TRAINING, TRAINING_REF, training.id)
            if poll is None:
                raise NotFound(ru.ATTENDANCE_NO_POLL)
            by_option, not_answered = await _tally(uow, poll)
            return AttendanceSummary(
                training=TrainingDTO.model_validate(training),
                by_option=by_option,
                not_answered=not_answered,
            )

    async def event_attendance(self, actor: UserContext, event_id: int) -> EventPollSummary:
        """Сводка опроса участия в матче, турнире или разборе игры."""
        async with self._uow_factory() as uow:
            event = await uow.events.get(event_id)
            if event is None:
                raise NotFound("Событие не найдено")
            require(actor, event.squad_id, Permission.ATTENDANCE)
            spec = EVENT_POLLS.get(event.type)
            poll = spec and await uow.polls.get_for_ref(spec.poll_type, EVENT_REF, event.id)
            if not poll:
                raise NotFound(ru.EVENT_NO_POLL)
            by_option, not_answered = await _tally(uow, poll)
            [dto] = await to_dtos(uow, [event])
            return EventPollSummary(
                event=dto,
                by_option=by_option,
                not_answered=not_answered,
            )


async def _tally(uow: UnitOfWork, poll: Poll) -> tuple[dict[str, list[str]], list[str]]:
    """Активные участники состава по вариантам ответа и не ответившие."""
    answers = await uow.polls.answers(poll.id)
    members = await uow.members.list_active_for_squad(poll.squad_id)
    options = [str(o) for o in poll.options]
    by_option: dict[str, list[str]] = {o: [] for o in options}
    not_answered: list[str] = []
    for _, user in members:
        name = to_dto(user).profile_html
        chosen = answers.get(user.id)
        if not chosen:
            not_answered.append(name)
            continue
        for idx in chosen:
            if 0 <= idx < len(options):
                by_option[options[idx]].append(name)
    return by_option, not_answered
