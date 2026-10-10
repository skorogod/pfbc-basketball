"""Сбор денег за игру: пост с суммой и реквизитами, опрос «Оплатил», напоминания после дедлайна.

Это не оплата тренировок по квитанциям mos.ru (ФТ-8) — другой процесс и другие таблицы.
Платят ответившие «Буду (игрок)» в опросе участия в матче: после дедлайна каждому, кто не
отметил «Оплатил», бот пишет в личку, а не дошедших — упоминает в группе (топик «Игры»).
Сколько раз и с каким интервалом — настройки состава; с 00:00 до 08:00 не напоминает.
"""

import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from pfbc_bot.db.models import Event, GamePayment, Poll, User
from pfbc_bot.domain.dates import due_payment_reminder, format_local, utcnow
from pfbc_bot.domain.enums import (
    EVENT_TYPE_TITLES,
    GAME_PAYMENT_PAID,
    GAME_PAYMENT_POLL_OPTIONS,
    MATCH_POLL_PLAYER,
    EventType,
    MembershipStatus,
    PollType,
    SquadRole,
    TopicPurpose,
)
from pfbc_bot.domain.payments import format_amount
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.logging import get_logger
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.events import EventDTO
from pfbc_bot.schemas.payments import GamePaymentCreate, GamePaymentDTO, PaymentDetails
from pfbc_bot.schemas.settings import MAX_PAYMENT_REMINDER_INTERVAL_HOURS, MAX_PAYMENT_REMINDERS
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.schemas.users import UserDTO
from pfbc_bot.services.common import (
    mention,
    profile,
    resolve_destination,
    send_direct,
    send_to_squad,
    squad_tz,
    to_dto,
)
from pfbc_bot.services.errors import Conflict, NotFound
from pfbc_bot.services.events import (
    EVENT_REF,
    GAME_PAYMENT_REF,
    format_announcement,
    format_when,
    to_dtos,
)
from pfbc_bot.services.permissions import Recipients, require
from pfbc_bot.services.ports import Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)

# игры для сбора: недавно сыгранные (собирают и после) и предстоящие
MATCHES_WINDOW = (timedelta(days=14), timedelta(days=60))
# напоминание давно пропущено (бот был выключен) — не догоняем
REMINDER_GRACE = timedelta(days=2)
# сборы, по которым ещё может быть напоминание: последнее — через (count − 1) × interval
REMINDER_LOOKBACK = (
    timedelta(hours=(MAX_PAYMENT_REMINDERS - 1) * MAX_PAYMENT_REMINDER_INTERVAL_HOURS)
    + REMINDER_GRACE
)


def reminder_key(payment_id: int, number: int = 0) -> str:
    """Отметка напоминания. У первого — ключ без номера, как до появления повторных."""
    base = f"game_payment:{payment_id}"
    return base if number == 0 else f"{base}:{number}"


@dataclass(frozen=True, slots=True)
class AmountSuggestion:
    """Сумма с игрока: стоимость игры турнира ÷ ответившие «Буду (игрок)» (вверх до рубля)."""

    game_price: Decimal
    players: int
    per_player: Decimal


@dataclass(frozen=True, slots=True)
class PaymentStatus:
    """Кто из игроков оплатил игру: только ответившие «Буду (игрок)»."""

    payment: GamePaymentDTO
    collector: str | None  # ссылка на профиль получателя
    paid: list[str]  # ссылки на профили
    unpaid: list[str]
    others_paid: list[str]  # отметили «Оплатил», но не «Буду (игрок)»


class GamePaymentService:
    def __init__(self, uow_factory: UowFactory, notifier: Notifier, recipients: Recipients) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier
        self._recipients = recipients

    async def collectors(self, actor: UserContext, squad_id: int) -> list[UserDTO]:
        """Кто может получать деньги: сам создающий (первым; реквизиты может ввести) и тренеры
        и админы состава, которые сами сохранили реквизиты. Чужие реквизиты не задаются."""
        require(actor, squad_id, Permission.GAME_PAYMENTS)
        async with self._uow_factory() as uow:
            me = await uow.users.get(actor.user.id)
            others = [u for u in await self._eligible(uow, squad_id) if u.id != actor.user.id]
            return [to_dto(u) for u in ([me] if me else []) + others]

    async def collector(self, actor: UserContext, squad_id: int, user_id: int) -> UserDTO:
        async with self._uow_factory() as uow:
            return to_dto(await self._collector(uow, actor, squad_id, user_id))

    async def _eligible(self, uow: UnitOfWork, squad_id: int) -> list[User]:
        """Тренеры и админы — участники состава с сохранёнными ими самими реквизитами."""
        roles = await uow.members.roles_in_squad(squad_id)
        return [
            user
            for member, user in await uow.members.list_active_for_squad(squad_id)
            if member.status == MembershipStatus.ACTIVE
            and user.payment_details
            and (SquadRole.COACH in roles.get(user.id, set()) or self._recipients.is_admin(user))
        ]

    async def _collector(
        self, uow: UnitOfWork, actor: UserContext, squad_id: int, user_id: int
    ) -> User:
        """Получатель: сам создающий или тренер/админ состава со своими реквизитами."""
        if user_id == actor.user.id:
            me = await uow.users.get(user_id)
            if me is None:
                raise NotFound(ru.MEMBER_NOT_FOUND)
            # собирать деньги — только тренер или админ (капитан — никогда)
            if not (actor.is_admin or actor.has_role(squad_id, SquadRole.COACH)):
                raise Conflict(ru.GAME_PAYMENT_COLLECTOR_NOT_ALLOWED)
            return me
        for user in await self._eligible(uow, squad_id):
            if user.id == user_id:
                return user
        raise Conflict(ru.GAME_PAYMENT_COLLECTOR_NOT_ALLOWED)

    @staticmethod
    def _details_for(
        actor: UserContext, collector: User, data: GamePaymentCreate
    ) -> PaymentDetails:
        """Реквизиты сбора. Свои — какие ввёл; чужие — только сохранённые им самим."""
        if collector.id == actor.user.id:
            return data.details
        if not collector.payment_details:
            raise Conflict(ru.GAME_PAYMENT_COLLECTOR_NOT_ALLOWED)
        return PaymentDetails.model_validate(collector.payment_details)

    async def default_deadline(
        self, actor: UserContext, squad_id: int, *, now: datetime | None = None
    ) -> datetime:
        """Срок по умолчанию — через game_payment_deadline_hours (сутки)."""
        require(actor, squad_id, Permission.GAME_PAYMENTS)
        async with self._uow_factory() as uow:
            settings = await load_squad_settings(uow, squad_id)
        return (now or utcnow()) + timedelta(hours=settings.game_payment_deadline_hours)

    async def matches(
        self, actor: UserContext, squad_id: int, *, now: datetime | None = None
    ) -> list[EventDTO]:
        """Матчи состава, за которые можно собрать деньги."""
        require(actor, squad_id, Permission.GAME_PAYMENTS)
        now = now or utcnow()
        back, ahead = MATCHES_WINDOW
        async with self._uow_factory() as uow:
            events = await uow.events.list_of_type(
                EventType.MATCH, now - back, now + ahead, [squad_id]
            )
            return await to_dtos(uow, events)

    async def create(
        self, actor: UserContext, data: GamePaymentCreate, *, now: datetime | None = None
    ) -> GamePaymentDTO:
        """Пост в топике «Игры»: игра, сумма, получатель и его реквизиты, срок — и опрос
        «Оплатил». Прежний сбор по этой игре и голосование участия в ней закрываются (платят
        ответившие «Буду (игрок)» — состав больше не меняется). Реквизиты запоминаются у получателя.

        В две фазы, чтобы отправка не держала транзакцию: проверки → пост и опрос в группе
        (опрос не принят — пост удаляется, сбор не создаётся) → под блокировкой матча запись
        сбора и закрытие прежнего (действующий сбор на матч — один, это гарантирует и БД)."""
        now = now or utcnow()
        if data.deadline_at is not None and data.deadline_at <= now:
            raise Conflict(ru.PAST_DATE)
        async with self._uow_factory() as uow:
            match = await self._match(uow, actor, data.match_event_id)
            destination = await resolve_destination(uow, match.squad_id, TopicPurpose.GAMES)
            if destination is None:
                raise Conflict(ru.POLL_NO_CHAT)
            collector = await self._collector(uow, actor, match.squad_id, data.collector_user_id)
            details = self._details_for(actor, collector, data)
            tz = squad_tz(await uow.squads.get(match.squad_id))
            [event] = await to_dtos(uow, [match])
            post = format_payment_post(event, data.amount, collector, details, data.deadline_at, tz)
            question = ru.GAME_PAYMENT_POLL_QUESTION.format(
                title=match.title, amount=format_amount(data.amount)
            )[:300]

        post_id = await self._notifier.send_message(
            destination.chat_id, post, thread_id=destination.thread_id
        )
        if post_id is None:
            raise Conflict(ru.GAME_PAYMENT_NOT_SENT)
        sent = await self._notifier.send_poll(
            destination.chat_id,
            question,
            GAME_PAYMENT_POLL_OPTIONS,
            thread_id=destination.thread_id,
        )
        if sent is None:  # пост без опроса в группе не оставляем
            await self._notifier.delete_message(destination.chat_id, post_id)
            raise Conflict(ru.GAME_PAYMENT_NOT_SENT)

        async with self._uow_factory() as uow:
            match = await self._match(uow, actor, data.match_event_id, lock=True)
            previous = await uow.game_payments.active_for_event(match.id)
            previous_poll = await self._cancel(uow, previous, now) if previous else None
            # сбор объявлен — состав на игру зафиксирован: голосование участия закрывается
            match_poll = await uow.polls.get_for_ref(PollType.MATCH, EVENT_REF, match.id)
            if match_poll is not None and match_poll.closed_at is None:
                match_poll.closed_at = now
            else:
                match_poll = None
            payment = await uow.game_payments.add(
                GamePayment(
                    squad_id=match.squad_id,
                    event_id=match.id,
                    amount=data.amount,
                    deadline_at=data.deadline_at,
                    details=details.model_dump(mode="json"),
                    created_by_user_id=actor.user.id,
                    collector_user_id=collector.id,
                )
            )
            await uow.polls.add(
                Poll(
                    type=PollType.GAME_PAYMENT,
                    squad_id=match.squad_id,
                    ref_type=GAME_PAYMENT_REF,
                    ref_id=payment.id,
                    tg_poll_id=sent.poll_id,
                    chat_id=destination.chat_id,
                    message_id=sent.message_id,
                    question=question,
                    options=list(GAME_PAYMENT_POLL_OPTIONS),
                    closes_at=None,  # «Оплатил» можно отметить и после срока
                )
            )
            if collector.id == actor.user.id:  # свои реквизиты — запоминаем; чужие не трогаем
                me = await uow.users.get(actor.user.id)
                if me is not None:
                    me.payment_details = details.model_dump(mode="json")
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="game_payment.create",
                entity="event",
                entity_id=match.id,
                payload={"payment_id": payment.id, "amount": str(data.amount)},
            )
            await uow.commit()
            dto = GamePaymentDTO.model_validate(payment)
        for poll in (previous_poll, match_poll):
            if poll is not None:
                await self._notifier.stop_poll(poll.chat_id, poll.message_id)
        return dto

    @staticmethod
    async def _match(
        uow: UnitOfWork, actor: UserContext, match_event_id: int, *, lock: bool = False
    ) -> Event:
        match = await uow.events.get(match_event_id, for_update=lock)
        if match is None or match.type != EventType.MATCH:
            raise NotFound(ru.TOURNAMENT_MATCH_NOT_FOUND)
        require(actor, match.squad_id, Permission.GAME_PAYMENTS)
        return match

    async def suggest_amount(
        self, actor: UserContext, match_event_id: int
    ) -> AmountSuggestion | None:
        """Подсказка суммы, если матч — в турнире со стоимостью игры и есть игроки."""
        async with self._uow_factory() as uow:
            match = await uow.events.get(match_event_id)
            if match is None or match.type != EventType.MATCH:
                raise NotFound(ru.TOURNAMENT_MATCH_NOT_FOUND)
            require(actor, match.squad_id, Permission.GAME_PAYMENTS)
            tournament = await uow.events.get(match.tournament_id) if match.tournament_id else None
            if tournament is None or tournament.game_price is None:
                return None
            players = await _players(uow, match.squad_id, match.id)
            if not players:
                return None
            price = tournament.game_price
            per_player = (price / len(players)).to_integral_value(rounding=ROUND_CEILING)
            return AmountSuggestion(game_price=price, players=len(players), per_player=per_player)

    async def preview(self, actor: UserContext, data: GamePaymentCreate) -> str:
        """Текст поста — показать тренеру перед публикацией."""
        async with self._uow_factory() as uow:
            match = await uow.events.get(data.match_event_id)
            if match is None or match.type != EventType.MATCH:
                raise NotFound(ru.TOURNAMENT_MATCH_NOT_FOUND)
            require(actor, match.squad_id, Permission.GAME_PAYMENTS)
            collector = await self._collector(uow, actor, match.squad_id, data.collector_user_id)
            details = self._details_for(actor, collector, data)
            tz = squad_tz(await uow.squads.get(match.squad_id))
            [event] = await to_dtos(uow, [match])
            return format_payment_post(event, data.amount, collector, details, data.deadline_at, tz)

    @staticmethod
    async def _cancel(uow: UnitOfWork, payment: GamePayment, now: datetime) -> Poll | None:
        payment.cancelled_at = now
        poll = await uow.polls.get_for_ref(PollType.GAME_PAYMENT, GAME_PAYMENT_REF, payment.id)
        if poll is not None:
            await uow.polls.supersede(poll, now)
        return poll

    async def saved_details(self, user_id: int) -> PaymentDetails | None:
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None or not user.payment_details:
                return None
            return PaymentDetails.model_validate(user.payment_details)

    async def save_details(self, actor: UserContext, details: PaymentDetails) -> None:
        """Свои реквизиты — задать или изменить их может только сам пользователь."""
        async with self._uow_factory() as uow:
            user = await uow.users.get(actor.user.id)
            if user is None:
                raise NotFound("Пользователь не найден")
            user.payment_details = details.model_dump(mode="json")
            await uow.commit()

    async def status(self, actor: UserContext, event_id: int) -> PaymentStatus | None:
        """Оплата игры для сводки: None — денег за эту игру не собирали."""
        async with self._uow_factory() as uow:
            match = await uow.events.get(event_id)
            if match is None:
                raise NotFound("Событие не найдено")
            require(actor, match.squad_id, Permission.GAME_PAYMENTS)  # оплату — тренер и админ
            payment = await uow.game_payments.active_for_event(match.id)
            if payment is None:
                return None
            players, paid = await _players_and_paid(uow, payment)
            others = [u for u in paid.values() if u.id not in players]
            collector = (
                await uow.users.get(payment.collector_user_id)
                if payment.collector_user_id
                else None
            )
            return PaymentStatus(
                payment=GamePaymentDTO.model_validate(payment),
                collector=profile(collector) if collector else None,
                paid=[profile(u) for u in players.values() if u.id in paid],
                unpaid=[profile(u) for u in players.values() if u.id not in paid],
                others_paid=[profile(u) for u in others],
            )

    async def remind_due(self, *, now: datetime | None = None) -> int:
        """Срок оплаты прошёл: игрокам без «Оплатил» — в личку (не дошло — в группу). Сколько
        раз и с каким интервалом — настройки состава, не в тихие часы. Каждое напоминание —
        один раз; отметка — до рассылки. Итог тренеру не шлётся: он виден в опросе."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            due = [p.id for p in await uow.game_payments.list_due(now - REMINDER_LOOKBACK, now)]
        reminded = 0
        for payment_id in due:
            async with self._uow_factory() as uow:
                payment = await uow.game_payments.get(payment_id)
                match = await uow.events.get(payment.event_id) if payment else None
                if (
                    payment is None
                    or payment.cancelled_at is not None
                    or payment.deadline_at is None
                    or match is None
                ):
                    continue
                settings = await load_squad_settings(uow, payment.squad_id)
                number = due_payment_reminder(
                    deadline_at=payment.deadline_at,
                    tz=squad_tz(await uow.squads.get(payment.squad_id)),
                    count=settings.game_payment_reminder_count,
                    interval=timedelta(hours=settings.game_payment_reminder_interval_hours),
                    now=now,
                    grace=REMINDER_GRACE,
                )
                if number is None:
                    continue
                key = reminder_key(payment.id, number)
                if not await uow.system.claim_reminder(key, "game_payment"):
                    continue
                await uow.commit()
                await self._remind(uow, payment, match)
                await uow.commit()  # dm_available
                reminded += 1
        return reminded

    async def _remind(self, uow: UnitOfWork, payment: GamePayment, match: Event) -> None:
        tz = squad_tz(await uow.squads.get(payment.squad_id))
        [event] = await to_dtos(uow, [match])
        details = PaymentDetails.model_validate(payment.details)
        players, paid = await _players_and_paid(uow, payment)
        unpaid = [u for u in players.values() if u.id not in paid]
        text = ru.GAME_PAYMENT_REMINDER_DM.format(
            event=_event_line(event, tz),
            amount=format_amount(payment.amount),
            details=details.text,
        )
        not_reached = [u for u in unpaid if not await send_direct(self._notifier, u, text)]
        if not_reached:
            await send_to_squad(
                uow,
                self._notifier,
                payment.squad_id,
                TopicPurpose.GAMES,
                ru.GAME_PAYMENT_REMINDER_GROUP.format(
                    names=", ".join(mention(u) for u in not_reached),
                    event=_event_line(event, tz),
                    amount=format_amount(payment.amount),
                    details=details.text,
                ),
            )
        log.info("game_payment_reminded", payment_id=payment.id, unpaid=len(unpaid))


async def _players_and_paid(
    uow: UnitOfWork, payment: GamePayment
) -> tuple[dict[int, User], dict[int, User]]:
    """Игроки — ответившие «Буду (игрок)» в опросе участия; оплатившие — «Оплатил».
    Только активные участники состава (вышедшим из группы не напоминаем)."""
    members = await _active_members(uow, payment.squad_id)
    player_ids = await _player_ids(uow, payment.event_id)
    paid_ids: set[int] = set()
    pay_poll = await uow.polls.get_for_ref(PollType.GAME_PAYMENT, GAME_PAYMENT_REF, payment.id)
    if pay_poll is not None:
        paid_idx = _option_index(pay_poll, GAME_PAYMENT_PAID)
        answers = await uow.polls.answers(pay_poll.id)
        paid_ids = {uid for uid, chosen in answers.items() if paid_idx in chosen}
    players = {uid: u for uid, u in members.items() if uid in player_ids}
    paid = {uid: u for uid, u in members.items() if uid in paid_ids}
    return players, paid


async def _active_members(uow: UnitOfWork, squad_id: int) -> dict[int, User]:
    return {
        user.id: user
        for member, user in await uow.members.list_active_for_squad(squad_id)
        if member.status == MembershipStatus.ACTIVE
    }


async def _player_ids(uow: UnitOfWork, match_event_id: int) -> set[int]:
    """Ответившие «Буду (игрок)» в опросе участия в матче."""
    match_poll = await uow.polls.get_for_ref(PollType.MATCH, EVENT_REF, match_event_id)
    if match_poll is None:
        return set()
    player_idx = _option_index(match_poll, MATCH_POLL_PLAYER)
    if player_idx is None:
        return set()
    answers = await uow.polls.answers(match_poll.id)
    return {uid for uid, chosen in answers.items() if player_idx in chosen}


async def _players(uow: UnitOfWork, squad_id: int, match_event_id: int) -> list[User]:
    members = await _active_members(uow, squad_id)
    player_ids = await _player_ids(uow, match_event_id)
    return [u for uid, u in members.items() if uid in player_ids]


def _option_index(poll: Poll, label: str) -> int | None:
    options = [str(o) for o in poll.options]
    return options.index(label) if label in options else None


def _event_line(event: EventDTO, tz: str) -> str:
    tournament = f" · 🏆 {html.escape(event.tournament_title)}" if event.tournament_title else ""
    return ru.GAME_PAYMENT_EVENT.format(
        type=EVENT_TYPE_TITLES[event.type],
        title=html.escape(event.title),
        when=format_when(event, tz),
        tournament=tournament,
    )


def format_payment_post(
    event: EventDTO,
    amount: Decimal,
    collector: User,
    details: PaymentDetails,
    deadline_at: datetime | None,
    tz: str,
) -> str:
    """Пост о сборе: игра со всей информацией, ниже — реквизиты, сумма и срок."""
    deadline = (
        ru.GAME_PAYMENT_DEADLINE.format(when=format_local(deadline_at, tz)) if deadline_at else ""
    )
    return ru.GAME_PAYMENT_POST.format(
        event=format_announcement(event, tz),
        amount=format_amount(amount),
        collector=profile(collector),
        details=details.text,
        deadline=deadline,
    )
