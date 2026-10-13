"""Сбор денег за игру: пост с суммой и реквизитами, опрос «Оплатил», напоминания после дедлайна.

Это не оплата тренировок по квитанциям mos.ru (ФТ-8) — другой процесс и другие таблицы.
Платят игроки **заявки** — её выбирает тренер (из ответивших «Буду (игрок)» и остальных
участников, не больше game_roster_max); заявка — источник истины для суммы, напоминаний и
«все оплатили», ответы в опросе участия на оплату не влияют. Перед постом сбора в топик
match публикуется «Состав на матч». После дедлайна каждому из заявки, кто не отметил
«Оплатил», бот пишет в личку, а не дошедших — упоминает в группе. Сколько раз и с каким
интервалом — настройки состава; с 00:00 до 08:00 не напоминает.

Пока сбор не начат, тренерам и админам состава приходит «Буду (игрок)» отметили N» с кнопкой
«Начать сбор» — когда набралось game_payment_squad_size. Дальше число меняется в этом же
сообщении (правка без уведомления — тренера не дёргаем новыми сообщениями).
"""

import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError

from pfbc_bot import callbacks
from pfbc_bot.db.models import Event, GamePayment, Poll, RosterNotice, User
from pfbc_bot.domain.dates import (
    WEEKDAY_SHORT,
    due_payment_reminder,
    format_local,
    to_local,
    utcnow,
)
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
from pfbc_bot.schemas.payments import (
    MAX_DETAILS,
    GamePaymentCreate,
    GamePaymentDTO,
    PaymentDetails,
    parse_details_list,
)
from pfbc_bot.schemas.settings import (
    MAX_PAYMENT_REMINDER_INTERVAL_HOURS,
    MAX_PAYMENT_REMINDERS,
    SquadSettings,
)
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.schemas.users import UserDTO
from pfbc_bot.services.common import (
    close_notice,
    mention,
    profile,
    resolve_destination,
    send_closable_notice,
    send_direct,
    send_to_squad,
    squad_tz,
    to_dto,
)
from pfbc_bot.services.errors import Conflict, NotFound
from pfbc_bot.services.events import (
    EVENT_REF,
    GAME_PAYMENT_REF,
    format_when,
    to_dtos,
)
from pfbc_bot.services.permissions import Recipients, require
from pfbc_bot.services.ports import InlineButton, Keyboard, Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)

# игры для сбора: недавно сыгранные (собирают и после) и предстоящие
MATCHES_WINDOW = (timedelta(days=14), timedelta(days=60))
# напоминание давно пропущено (бот был выключен) — не догоняем
REMINDER_GRACE = timedelta(days=2)
# сборы, по которым ещё может быть напоминание: последнее — через (count − 1) × interval
# уведомления «отметились N игроков» — по матчам в ближайшие столько дней
ROSTER_NOTICE_HORIZON = timedelta(days=60)
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
    closed: bool  # сбор завершён: опрос закрыт, напоминаний нет


@dataclass(frozen=True, slots=True)
class RosterCandidate:
    user: UserDTO
    voted: bool  # ответил «Буду (игрок)»


@dataclass(frozen=True, slots=True)
class RosterChoice:
    """Выбор заявки: ответившие «Буду (игрок)» (по времени ответа), затем остальные активные
    участники (по имени); preselected — первые max_players ответивших."""

    event: EventDTO
    tz: str
    candidates: list[RosterCandidate]
    preselected: list[int]
    max_players: int
    has_payment: bool  # сбор уже объявлен


@dataclass(frozen=True, slots=True)
class PaymentPreview:
    roster: str  # «Состав на матч»
    post: str  # пост сбора


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
        """Реквизиты сбора. Свои — какие ввёл; чужие — только одни из сохранённых им самим
        (подменить их, передав другие, нельзя)."""
        if collector.id == actor.user.id:
            return data.details
        for saved in parse_details_list(collector.payment_details):
            if saved.same_as(data.details):
                return saved
        raise Conflict(ru.GAME_PAYMENT_COLLECTOR_NOT_ALLOWED)

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
        """В топик match: «Состав на матч» (заявка), пост сбора (игра, сумма, получатель,
        реквизиты, срок) и опрос «Оплатил». Голосование участия закрывается — состав
        зафиксирован. Прежний сбор по игре заменяется (replace_active) или — отказ.

        В две фазы, чтобы отправка не держала транзакцию: проверки → сообщения в группе (что-то
        не принято — отправленное удаляется, сбор не создаётся) → под блокировкой матча запись
        сбора с заявкой и закрытие прежнего (действующий сбор на матч — один, это гарантирует
        и БД). Реквизиты здесь не запоминаются — тренер сохраняет их сам (вопрос после ввода)."""
        now = now or utcnow()
        if data.deadline_at is not None and data.deadline_at <= now:
            raise Conflict(ru.PAST_DATE)
        async with self._uow_factory() as uow:
            match = await self._match(uow, actor, data.match_event_id)
            if not data.replace_active and await uow.game_payments.active_for_event(match.id):
                raise Conflict(ru.GAME_PAYMENT_ALREADY)
            destination = await resolve_destination(uow, match.squad_id, TopicPurpose.MATCH)
            if destination is None:
                raise Conflict(ru.POLL_NO_CHAT)
            collector = await self._collector(uow, actor, match.squad_id, data.collector_user_id)
            details = self._details_for(actor, collector, data)
            players = await _roster_players(uow, match.squad_id, data.player_ids)
            tz = squad_tz(await uow.squads.get(match.squad_id))
            [event] = await to_dtos(uow, [match])
            roster_post = format_roster_post(event, players, tz)
            post = format_payment_post(
                event, data.amount, collector, details, data.deadline_at, tz, len(players)
            )
            question = ru.GAME_PAYMENT_POLL_QUESTION.format(
                title=match.title, amount=format_amount(data.amount)
            )[:300]

        chat_id, thread_id = destination.chat_id, destination.thread_id
        sent_ids: list[int] = []  # что удалить, если сбор не состоится
        for text in (roster_post, post):
            message_id = await self._notifier.send_message(chat_id, text, thread_id=thread_id)
            if message_id is None:
                await self._delete(chat_id, sent_ids)
                raise Conflict(ru.GAME_PAYMENT_NOT_SENT)
            sent_ids.append(message_id)
        roster_id = sent_ids[0]
        sent = await self._notifier.send_poll(
            chat_id, question, GAME_PAYMENT_POLL_OPTIONS, thread_id=thread_id
        )
        if sent is None:  # состав и пост без опроса в группе не оставляем
            await self._delete(chat_id, sent_ids)
            raise Conflict(ru.GAME_PAYMENT_NOT_SENT)

        async with self._uow_factory() as uow:
            match = await self._match(uow, actor, data.match_event_id, lock=True)
            previous = await uow.game_payments.active_for_event(match.id)
            if previous is not None and not data.replace_active:
                # пока отправляли, сбор объявил другой тренер — наш убираем
                await uow.rollback()
                await self._delete(chat_id, [*sent_ids, sent.message_id])
                raise Conflict(ru.GAME_PAYMENT_ALREADY)
            previous_poll = await self._cancel(uow, previous, now) if previous else None
            previous_roster = previous.roster_message_id if previous else None
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
                    roster_message_id=roster_id,
                )
            )
            await uow.game_payments.add_players(payment.id, data.player_ids)
            await uow.polls.add(
                Poll(
                    type=PollType.GAME_PAYMENT,
                    squad_id=match.squad_id,
                    ref_type=GAME_PAYMENT_REF,
                    ref_id=payment.id,
                    tg_poll_id=sent.poll_id,
                    chat_id=chat_id,
                    message_id=sent.message_id,
                    question=question,
                    options=list(GAME_PAYMENT_POLL_OPTIONS),
                    closes_at=None,  # «Оплатил» можно отметить и после срока
                )
            )
            hidden = await _hide_notices(uow, now, event_id=match.id)
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="game_payment.create",
                entity="event",
                entity_id=match.id,
                payload={
                    "payment_id": payment.id,
                    "amount": str(data.amount),
                    "players": len(data.player_ids),
                },
            )
            await uow.commit()
            dto = GamePaymentDTO.model_validate(payment)
        for poll in (previous_poll, match_poll):
            if poll is not None:
                await self._notifier.stop_poll(poll.chat_id, poll.message_id)
        if previous_roster is not None:  # прежний состав в группе больше не действует
            await self._notifier.delete_message(chat_id, previous_roster)
        await self._strip_buttons(hidden)
        return dto

    async def _delete(self, chat_id: int, message_ids: list[int]) -> None:
        for message_id in message_ids:
            await self._notifier.delete_message(chat_id, message_id)

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
        self, actor: UserContext, match_event_id: int, players: int
    ) -> AmountSuggestion | None:
        """Подсказка суммы с игрока: стоимость игры турнира ÷ заявка (вверх до рубля); None —
        матч не в турнире или стоимость не указана."""
        if players < 1:
            return None
        async with self._uow_factory() as uow:
            match = await self._match(uow, actor, match_event_id)
            price = await _game_price(uow, match)
            return _suggestion(price, players) if price is not None else None

    async def roster(self, actor: UserContext, match_event_id: int) -> RosterChoice:
        """Кандидаты в заявку: ответившие «Буду (игрок)», затем остальные участники."""
        async with self._uow_factory() as uow:
            match = await self._match(uow, actor, match_event_id)
            settings = await load_squad_settings(uow, match.squad_id)
            members = await _active_members(uow, match.squad_id)
            voted = [uid for uid in await _voted_ids(uow, match.id) if uid in members]
            others = sorted(
                (u for uid, u in members.items() if uid not in set(voted)), key=_name_key
            )
            [event] = await to_dtos(uow, [match])
            return RosterChoice(
                event=event,
                tz=squad_tz(await uow.squads.get(match.squad_id)),
                candidates=[RosterCandidate(to_dto(members[uid]), True) for uid in voted]
                + [RosterCandidate(to_dto(u), False) for u in others],
                preselected=voted[: settings.game_roster_max],
                max_players=settings.game_roster_max,
                has_payment=await uow.game_payments.active_for_event(match.id) is not None,
            )

    async def start_collection(
        self, actor: UserContext, match_event_id: int, *, now: datetime | None = None
    ) -> None:
        """«Начать сбор» из уведомления: состав фиксируется — голосование участия закрывается,
        кнопки в уведомлениях снимаются. Повторно (тренер бросил выбор заявки) — сценарий
        продолжается. Уже объявленный сбор не перебивается; получатель денег — сам нажавший,
        это проверяется до закрытия опроса."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            match = await self._match(uow, actor, match_event_id, lock=True)
            await self._collector(uow, actor, match.squad_id, actor.user.id)
            if await uow.game_payments.active_for_event(match.id) is not None:
                raise Conflict(ru.GAME_PAYMENT_ALREADY)
            poll = await uow.polls.get_for_ref(PollType.MATCH, EVENT_REF, match.id)
            to_stop = poll if poll is not None and poll.closed_at is None else None
            if to_stop is not None:
                to_stop.closed_at = now
            hidden = await _hide_notices(uow, now, event_id=match.id)
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="game_payment.start",
                entity="event",
                entity_id=match.id,
            )
            await uow.commit()
        if to_stop is not None:
            await self._notifier.stop_poll(to_stop.chat_id, to_stop.message_id)
        await self._strip_buttons(hidden)

    async def preview(self, actor: UserContext, data: GamePaymentCreate) -> PaymentPreview:
        """«Состав на матч» и пост сбора — показать тренеру перед публикацией."""
        async with self._uow_factory() as uow:
            match = await self._match(uow, actor, data.match_event_id)
            collector = await self._collector(uow, actor, match.squad_id, data.collector_user_id)
            details = self._details_for(actor, collector, data)
            players = await _roster_players(uow, match.squad_id, data.player_ids)
            tz = squad_tz(await uow.squads.get(match.squad_id))
            [event] = await to_dtos(uow, [match])
            return PaymentPreview(
                roster=format_roster_post(event, players, tz, in_group=False),
                post=format_payment_post(
                    event,
                    data.amount,
                    collector,
                    details,
                    data.deadline_at,
                    tz,
                    len(players),
                    in_group=False,
                ),
            )

    @staticmethod
    async def _cancel(uow: UnitOfWork, payment: GamePayment, now: datetime) -> Poll | None:
        payment.cancelled_at = now
        poll = await uow.polls.get_for_ref(PollType.GAME_PAYMENT, GAME_PAYMENT_REF, payment.id)
        if poll is not None:
            await uow.polls.supersede(poll, now)
        return poll

    # --- «Буду (игрок)» отметили N: тренерам и админам, кнопка «Начать сбор» ---
    async def send_roster_updates(self, *, now: datetime | None = None) -> int:
        """Тик worker-а (раз в минуту): по будущим матчам с открытым голосованием и без сбора.
        «Буду (игрок)» набралось game_payment_squad_size — тренерам и админам сообщение с
        кнопкой «Начать сбор»; дальше число меняется в том же сообщении (изменения за минуту —
        одной правкой). Возвращает число матчей, по которым отправлено или обновлено."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            match_ids = [
                m.id
                for m in await uow.events.list_of_type(
                    EventType.MATCH, now, now + ROSTER_NOTICE_HORIZON
                )
            ]
        changed = 0
        for match_id in match_ids:
            changed += await self._roster_update(match_id, now)
        await self._hide_stale_notices(now)
        return changed

    async def _roster_update(self, match_id: int, now: datetime) -> int:
        """В три фазы, как остальные задачи: (1) отметка в БД и commit — новые строки
        уведомлений (уникальность «опрос + получатель» не даст второму worker создать их ещё
        раз) или новое число в существующих; (2) отправка и правка в Telegram — вне транзакции;
        (3) результат (message_id, недоставлено). Сбой между фазами — сообщение скорее не уйдёт,
        чем уйдёт дважды."""
        async with self._uow_factory() as uow:
            match = await uow.events.get(match_id)
            if match is None or match.starts_at is None or match.starts_at <= now:
                return 0
            settings = await load_squad_settings(uow, match.squad_id)
            if settings.game_payment_squad_size == 0:
                return 0
            poll = await uow.polls.get_for_ref(PollType.MATCH, EVENT_REF, match.id)
            if poll is None or poll.closed_at is not None:
                return 0
            if await uow.game_payments.active_for_event(match.id) is not None:
                return 0
            count = await _voted_count(uow, match.squad_id, match.id)
            notices = await uow.game_payments.notices(poll.id)
            if not notices and count < settings.game_payment_squad_size:
                return 0
            if notices and notices[-1].players == count:
                return 0
            tz = squad_tz(await uow.squads.get(match.squad_id))
            [event] = await to_dtos(uow, [match])
            text = format_roster_notice(
                event, tz, count, settings, await _game_price(uow, match) is not None, now
            )
            edit = bool(notices)
            if edit:
                for notice in notices:
                    notice.players = count
                    if _deliverable(notice):
                        notice.text = text
            else:
                notices = [
                    await uow.game_payments.add_notice(
                        RosterNotice(
                            event_id=match.id,
                            poll_id=poll.id,
                            user_id=user.id,
                            chat_id=user.tg_user_id,
                            message_id=None,  # ещё не отправлено
                            text=text,
                            players=count,
                        )
                    )
                    for user in await self._roster_recipients(uow, match.squad_id)
                ]
            try:
                await uow.commit()
            except IntegrityError:  # уведомления по опросу уже завёл параллельный worker
                await uow.rollback()
                return 0
            pending = [
                (n.id, n.chat_id, n.message_id) for n in notices if _deliverable(n) or not edit
            ]
        results = await self._deliver_notices(pending, text, _start_keyboard(match_id))
        async with self._uow_factory() as uow:
            for notice_id, message_id in results.items():
                stored = await uow.game_payments.get_notice(notice_id)
                if stored is None:
                    continue
                if message_id is not None:
                    stored.message_id = message_id
                elif stored.message_id is None:  # первое сообщение не дошло — не повторяем
                    stored.hidden_at = now
                if user := await uow.users.get(stored.user_id):
                    user.dm_available = stored.message_id is not None
            await uow.commit()
        log.info("roster_notice", event_id=match_id, players=count, edited=edit)
        return 1

    async def _deliver_notices(
        self, pending: list[tuple[int, int, int | None]], text: str, keyboard: Keyboard
    ) -> dict[int, int | None]:
        """notice_id → новый message_id (None — сообщение не отправлялось или не дошло).
        Есть сообщение — правка (без уведомления); правка не прошла (сообщение удалили) или
        сообщения ещё нет — новое сообщение."""
        results: dict[int, int | None] = {}
        for notice_id, chat_id, message_id in pending:
            if message_id is not None and await self._notifier.edit_message(
                chat_id, message_id, text, keyboard=keyboard
            ):
                results[notice_id] = None
                continue
            results[notice_id] = await self._notifier.send_message(chat_id, text, keyboard=keyboard)
        return results

    async def _roster_recipients(self, uow: UnitOfWork, squad_id: int) -> list[User]:
        """Тренерам (право сбора денег) и админам — участникам состава; нет таких — корневым
        админам (как Recipients)."""
        holders = await self._recipients.with_permission(uow, squad_id, Permission.GAME_PAYMENTS)
        admins = [
            u
            for u in (await _active_members(uow, squad_id)).values()
            if self._recipients.is_admin(u)
        ]
        return list({u.id: u for u in [*holders, *admins]}.values())

    async def _hide_stale_notices(self, now: datetime) -> None:
        """Кнопки уведомлений, по которым начать сбор уже нельзя или незачем: голосование
        закрыто или устарело (матч перенесли), сбор начат, матч удалён или начался."""
        async with self._uow_factory() as uow:
            stale: list[RosterNotice] = []
            for notice in await uow.game_payments.visible_notices():
                poll = await uow.polls.get_by_id(notice.poll_id)
                match = await uow.events.get(notice.event_id)
                if (
                    poll is None
                    or poll.closed_at is not None
                    or poll.superseded_at is not None
                    or match is None
                    or match.starts_at is None
                    or match.starts_at <= now
                    or await uow.game_payments.active_for_event(notice.event_id) is not None
                ):
                    notice.hidden_at = now
                    stale.append(notice)
            await uow.commit()
        await self._strip_buttons(stale)

    async def _strip_buttons(self, notices: list[RosterNotice]) -> None:
        for notice in notices:
            if notice.message_id is not None:
                await self._notifier.edit_message(notice.chat_id, notice.message_id, notice.text)

    # --- реквизиты: задаёт, меняет и удаляет только сам пользователь ---
    async def details_of(self, user_id: int) -> list[PaymentDetails]:
        """Сохранённые реквизиты пользователя (для показа и выбора; не для изменения)."""
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            return parse_details_list(user.payment_details) if user else []

    async def add_details(self, actor: UserContext, details: PaymentDetails) -> PaymentDetails:
        """Добавляет свои реквизиты (такие же уже есть — возвращает их)."""
        async with self._uow_factory() as uow:
            me = await self._me(uow, actor)
            existing = parse_details_list(me.payment_details)
            same = next((d for d in existing if d.same_as(details)), None)
            if same is not None:
                return same
            if len(existing) >= MAX_DETAILS:
                raise Conflict(ru.DETAILS_LIMIT.format(limit=MAX_DETAILS))
            _store(me, [*existing, details])
            await uow.commit()
            return details

    async def update_details(
        self, actor: UserContext, details_id: str, details: PaymentDetails
    ) -> PaymentDetails:
        async with self._uow_factory() as uow:
            me = await self._me(uow, actor)
            existing = parse_details_list(me.payment_details)
            if not any(d.id == details_id for d in existing):
                raise NotFound(ru.DETAILS_NOT_FOUND)
            updated = details.model_copy(update={"id": details_id})
            _store(me, [updated if d.id == details_id else d for d in existing])
            await uow.commit()
            return updated

    async def delete_details(self, actor: UserContext, details_id: str) -> None:
        async with self._uow_factory() as uow:
            me = await self._me(uow, actor)
            existing = parse_details_list(me.payment_details)
            if not any(d.id == details_id for d in existing):
                raise NotFound(ru.DETAILS_NOT_FOUND)
            _store(me, [d for d in existing if d.id != details_id])
            await uow.commit()

    @staticmethod
    async def _me(uow: UnitOfWork, actor: UserContext) -> User:
        user = await uow.users.get(actor.user.id)
        if user is None:
            raise NotFound("Пользователь не найден")
        return user

    # --- все оплатили ---
    async def notify_if_all_paid(self, payment_id: int, *, now: datetime | None = None) -> bool:
        """Все ответившие «Буду (игрок)» отметили «Оплатил» — получателю денег и создателю
        сбора сообщение (один раз на сбор). True — оповещение отправлено сейчас."""
        async with self._uow_factory() as uow:
            payment = await uow.game_payments.get(payment_id)
            if payment is None or payment.cancelled_at is not None:
                return False
            match = await uow.events.get(payment.event_id)
            if match is None:
                return False
            players, paid = await _players_and_paid(uow, payment)
            if not players or any(uid not in paid for uid in players):
                return False
            key = f"game_payment_paid:{payment.id}"
            if not await uow.system.claim_reminder(key, "game_payment"):
                return False
            await uow.commit()
            tz = squad_tz(await uow.squads.get(payment.squad_id))
            [event] = await to_dtos(uow, [match])
            text = ru.GAME_PAYMENT_ALL_PAID.format(
                event=_event_line(event, tz, with_tournament=False),
                count=len(players),
                amount=format_amount(payment.amount),
                names=", ".join(profile(u) for u in players.values()),
            )
            close = InlineButton(
                ru.PAYMENT_BTN_CLOSE, callback_data=f"{callbacks.GAME_PAYMENT_CLOSE}:{payment.id}"
            )
            # запоминается — чтобы снять кнопку, когда сбор завершат
            payment.paid_notice = await self._send_to_owners(uow, payment, text, [[close]])
            await uow.commit()  # dm_available
        log.info("game_payment_all_paid", payment_id=payment_id)
        return True

    async def _send_to_owners(
        self, uow: UnitOfWork, payment: GamePayment, text: str, keyboard: Keyboard
    ) -> dict[str, Any] | None:
        """Получателю денег и создавшему сбор; не дошло никому — тем, кто собирает деньги.
        Результат — для close_notice."""
        ids = {i for i in (payment.collector_user_id, payment.created_by_user_id) if i}
        owners = await uow.users.list_by_ids(sorted(ids))
        sent = await send_closable_notice(self._notifier, owners, text, keyboard)
        if sent is None:
            staff = await self._recipients.with_permission(
                uow, payment.squad_id, Permission.GAME_PAYMENTS
            )
            sent = await send_closable_notice(self._notifier, staff, text, keyboard)
        return sent

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
            poll = await uow.polls.get_for_ref(PollType.GAME_PAYMENT, GAME_PAYMENT_REF, payment.id)
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
                closed=poll is None or poll.closed_at is not None,
            )

    async def close(
        self, actor: UserContext, payment_id: int, *, now: datetime | None = None
    ) -> bool:
        """Завершить сбор: опрос «Оплатил» в группе закрывается, напоминаний больше нет.
        False — сбор уже завершён."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            payment = await uow.game_payments.get(payment_id)
            if payment is None or payment.cancelled_at is not None:
                raise NotFound(ru.GAME_PAYMENT_NOT_FOUND)
            require(actor, payment.squad_id, Permission.GAME_PAYMENTS)
            poll = await uow.polls.get_for_ref(PollType.GAME_PAYMENT, GAME_PAYMENT_REF, payment.id)
            if poll is None or poll.closed_at is not None:
                return False
            poll.closed_at = now
            notice, payment.paid_notice = payment.paid_notice, None
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="game_payment.close",
                entity="event",
                entity_id=payment.event_id,
                payload={"payment_id": payment.id},
            )
            await uow.commit()
        await self._notifier.stop_poll(poll.chat_id, poll.message_id)
        await close_notice(self._notifier, notice)  # «Все оплатили»: кнопка больше не нужна
        return True

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
                poll = await uow.polls.get_for_ref(
                    PollType.GAME_PAYMENT, GAME_PAYMENT_REF, payment.id
                )
                if poll is None or poll.closed_at is not None:  # сбор завершён
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
                TopicPurpose.MATCH,
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
    """Игроки — заявка сбора (у сборов до появления заявки — ответившие «Буду (игрок)»);
    оплатившие — «Оплатил». Только активные участники состава (вышедшим не напоминаем)."""
    members = await _active_members(uow, payment.squad_id)
    roster = await uow.game_payments.player_ids(payment.id)
    player_ids = roster if roster is not None else set(await _voted_ids(uow, payment.event_id))
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


async def _voted_ids(uow: UnitOfWork, match_event_id: int) -> list[int]:
    """Ответившие «Буду (игрок)» в действующем опросе участия — по времени ответа."""
    match_poll = await uow.polls.get_for_ref(PollType.MATCH, EVENT_REF, match_event_id)
    if match_poll is None:
        return []
    player_idx = _option_index(match_poll, MATCH_POLL_PLAYER)
    if player_idx is None:
        return []
    return [
        uid
        for uid, chosen in await uow.polls.answers_by_time(match_poll.id)
        if player_idx in chosen
    ]


async def _voted_count(uow: UnitOfWork, squad_id: int, match_event_id: int) -> int:
    """Сколько активных участников ответили «Буду (игрок)»."""
    members = await _active_members(uow, squad_id)
    return sum(uid in members for uid in await _voted_ids(uow, match_event_id))


async def _roster_players(uow: UnitOfWork, squad_id: int, player_ids: list[int]) -> list[User]:
    """Заявка: не больше game_roster_max, только активные участники состава."""
    settings = await load_squad_settings(uow, squad_id)
    if len(player_ids) > settings.game_roster_max:
        raise Conflict(ru.ROSTER_TOO_MANY.format(max=settings.game_roster_max))
    members = await _active_members(uow, squad_id)
    if any(uid not in members for uid in player_ids):
        raise Conflict(ru.ROSTER_NOT_MEMBERS)
    return [members[uid] for uid in player_ids]


async def _game_price(uow: UnitOfWork, match: Event) -> Decimal | None:
    tournament = await uow.events.get(match.tournament_id) if match.tournament_id else None
    return tournament.game_price if tournament is not None else None


def _suggestion(price: Decimal, players: int) -> AmountSuggestion:
    per_player = (price / players).to_integral_value(rounding=ROUND_CEILING)
    return AmountSuggestion(game_price=price, players=players, per_player=per_player)


def _deliverable(notice: RosterNotice) -> bool:
    """Сообщение с кнопкой есть и кнопка не снята — его можно править."""
    return notice.hidden_at is None and notice.message_id is not None


def _start_keyboard(match_id: int) -> Keyboard:
    return [
        [
            InlineButton(
                ru.ROSTER_BTN_START,
                callbacks.pack(callbacks.ROSTER, callbacks.ROSTER_START, match_id),
            )
        ]
    ]


def _name_key(user: User) -> str:
    return to_dto(user).display_name.casefold()


async def _hide_notices(
    uow: UnitOfWork, now: datetime, *, poll_id: int | None = None, event_id: int | None = None
) -> list[RosterNotice]:
    """Снять кнопку «Начать сбор» (в БД; в Telegram — _strip_buttons после commit)."""
    notices = await uow.game_payments.visible_notices(poll_id=poll_id, event_id=event_id)
    for notice in notices:
        notice.hidden_at = now
    return notices


def _option_index(poll: Poll, label: str) -> int | None:
    options = [str(o) for o in poll.options]
    return options.index(label) if label in options else None


def _event_line(event: EventDTO, tz: str, *, with_tournament: bool = True) -> str:
    """«Матч: <b>название</b>, дата» — без места: оно в закреплённом посте о матче.
    with_tournament=False — тренеру: турнир своей игры он знает."""
    tournament = (
        f" · 🏆 {html.escape(event.tournament_title)}"
        if with_tournament and event.tournament_title
        else ""
    )
    return ru.GAME_PAYMENT_EVENT.format(
        type=EVENT_TYPE_TITLES[event.type],
        title=html.escape(event.title),
        when=format_match_when(event, tz),
        tournament=tournament,
    )


def format_payment_post(
    event: EventDTO,
    amount: Decimal,
    collector: User,
    details: PaymentDetails,
    deadline_at: datetime | None,
    tz: str,
    players: int,
    *,
    in_group: bool = True,
) -> str:
    """Пост о сборе: игра (название и дата — место есть в закреплённом посте о ней), ниже —
    реквизиты, сумма и срок. Список игроков — в «Составе на матч» над постом. in_group=False —
    предпросмотр в личке (см. _dm_link)."""
    deadline = (
        ru.GAME_PAYMENT_DEADLINE.format(when=format_local(deadline_at, tz)) if deadline_at else ""
    )
    return ru.GAME_PAYMENT_POST.format(
        title=html.escape(event.title),
        when=format_match_when(event, tz),
        amount=format_amount(amount),
        collector=mention(collector) if in_group else _dm_link(collector),
        details=details.text,
        deadline=deadline,
        players=players,
    )


def format_match_when(event: EventDTO, tz: str) -> str:
    """«сб, 18.10 · 12:30»."""
    if event.starts_at is None:
        return format_when(event, tz)
    local = to_local(event.starts_at, tz)
    return f"{WEEKDAY_SHORT[local.weekday()]}, {local:%d.%m · %H:%M}"


def format_roster_post(
    event: EventDTO, players: list[User], tz: str, *, in_group: bool = True
) -> str:
    """«Состав на матч»: игра (название и дата — место есть в закреплённом посте о ней) и кто
    играет. Игроки — по алфавиту; в группе — упоминаниями (включённые получают уведомление),
    в личке (in_group=False, предпросмотр) — см. _dm_link."""
    names = sorted(players, key=_name_key)
    name = mention if in_group else _dm_link
    return ru.ROSTER_POST.format(
        title=html.escape(event.title),
        when=format_match_when(event, tz),
        count=len(names),
        players="\n".join(f"{n}. {name(u)}" for n, u in enumerate(names, 1)),
    )


def _dm_link(user: User) -> str:
    """Имя для предпросмотра в личке. Упоминание tg://user Telegram показывает в личке только
    для тех, кто писал боту, — у остальных ссылка молча пропадает. Поэтому при username —
    ссылка t.me (открывается всегда), иначе — как в profile_link, но без пометки «не в боте»:
    предпросмотр повторяет пост для группы, где ссылки будут у всех."""
    dto = to_dto(user)
    if dto.username or dto.knows_bot:
        return dto.profile_html
    return html.escape(dto.display_name)


def format_roster_notice(
    event: EventDTO,
    tz: str,
    count: int,
    settings: SquadSettings,
    has_price: bool,
    now: datetime,
) -> str:
    """Тренеру/админу: сколько отметили «Буду (игрок)» сейчас и что учесть."""
    hints = []
    if count > settings.game_roster_max:
        hints.append(ru.ROSTER_NOTICE_TOO_MANY.format(max=settings.game_roster_max))
    if count < settings.game_payment_squad_size:
        hints.append(
            ru.ROSTER_NOTICE_SHORT.format(
                size=settings.game_payment_squad_size,
                missing=settings.game_payment_squad_size - count,
            )
        )
    if not has_price:
        hints.append(ru.ROSTER_NOTICE_NO_PRICE)
    return ru.ROSTER_NOTICE.format(
        title=html.escape(event.title),
        when=format_match_when(event, tz),
        count=count,
        hints="".join(f"\n{h}" for h in hints),
        updated=f"{to_local(now, tz):%d.%m %H:%M}",
    )


def _store(user: User, details: list[PaymentDetails]) -> None:
    user.payment_details = [d.model_dump(mode="json") for d in details] or None
