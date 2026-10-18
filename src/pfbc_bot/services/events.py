"""Журнал событий состава: создание с проверкой пересечений, публикация, опрос участия,
напоминания (ФТ-9), турниры, предложение разбора после матча (ФТ-5)."""

import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from pfbc_bot import callbacks
from pfbc_bot.db.models import Event, Poll
from pfbc_bot.domain.dates import (
    due_reminder_offsets,
    format_interval,
    format_local,
    local_to_utc,
    overlaps,
    review_default_at,
    to_local,
    utcnow,
)
from pfbc_bot.domain.enums import (
    EVENT_POLL_OPTIONS,
    EVENT_TYPE_EMOJI,
    EVENT_TYPE_TITLES,
    MATCH_DURATION_MINUTES,
    MATCH_POLL_OPTIONS,
    TOURNAMENT_POLL_OPTIONS,
    EventType,
    PollType,
    TopicPurpose,
)
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.logging import get_logger
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.events import (
    EventCreate,
    EventDTO,
    ReviewProposal,
    ScheduleConflict,
    TournamentCard,
)
from pfbc_bot.schemas.squads import Destination, UserContext
from pfbc_bot.services.common import resolve_destination, send_to_squad, squad_tz
from pfbc_bot.services.errors import Conflict, NotFound, PermissionDenied
from pfbc_bot.services.permissions import Recipients, require
from pfbc_bot.services.ports import InlineButton, Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)

REMINDER_HORIZON = timedelta(days=31)
REMINDER_GRACE = timedelta(hours=12)
# Самое длинное мероприятие — сутки: всё, что началось раньше, с новым событием не пересечётся.
CONFLICT_LOOKBACK = timedelta(days=1)
GAME_EVENT_TYPES = frozenset({EventType.MATCH, EventType.TOURNAMENT, EventType.REVIEW})
EVENT_REF = "event"
GAME_PAYMENT_REF = "game_payment"  # опрос «Оплатил» сбора денег за игру
# разбор игры по умолчанию — за час до первой тренировки недели
REVIEW_LEAD = timedelta(hours=1)
# свободные матчи для привязки к турниру — начиная с месяца назад
TOURNAMENT_MATCHES_LOOKBACK = timedelta(days=30)
TOURNAMENTS_WINDOW = (timedelta(days=180), timedelta(days=365))  # назад, вперёд
REVIEW_DURATION_MINUTES = 60
# предложение разбора — по матчам, закончившимся не раньше, чем столько назад
REVIEW_OFFER_WINDOW = timedelta(days=3)
TITLE_MAX, DIVISION_MAX = 200, 100


def review_offer_key(match_event_id: int) -> str:
    return f"review_offer:{match_event_id}"


def review_key(match_event_id: int) -> str:
    """Разбор по матчу назначен — второй (двойное нажатие, другой тренер) не создаётся."""
    return f"review:{match_event_id}"


@dataclass(frozen=True, slots=True)
class EventPollSpec:
    poll_type: PollType
    options: tuple[str, ...]
    question: str


# События, к посту о которых в группе добавляется опрос участия.
EVENT_POLLS: dict[EventType, EventPollSpec] = {
    EventType.MATCH: EventPollSpec(PollType.MATCH, MATCH_POLL_OPTIONS, ru.EVENT_POLL_QUESTION),
    EventType.REVIEW: EventPollSpec(PollType.REVIEW, EVENT_POLL_OPTIONS, ru.EVENT_POLL_QUESTION),
    EventType.TOURNAMENT: EventPollSpec(
        PollType.TOURNAMENT, TOURNAMENT_POLL_OPTIONS, ru.TOURNAMENT_POLL_QUESTION
    ),
}


def event_permission(event_type: EventType) -> Permission:
    """Собрание — право «собрания», остальные события (игры, турниры, разборы…) — «события»."""
    return Permission.MEETINGS if event_type == EventType.MEETING else Permission.EVENTS


def event_topic(event_type: EventType) -> TopicPurpose:
    """Топик группы для поста и напоминаний: игровые события — в match, собрания — в
    teambuilding, остальные — в общий (нет своего топика — тоже в общий)."""
    if event_type in GAME_EVENT_TYPES:
        return TopicPurpose.MATCH
    if event_type == EventType.MEETING:
        return TopicPurpose.TEAMBUILDING
    return TopicPurpose.GENERAL


class ScheduleConflictError(Conflict):
    """Событие пересекается с тренировкой, матчем или другим событием, и это не подтверждено."""

    def __init__(self, conflicts: list[ScheduleConflict], tz: str) -> None:
        self.conflicts = conflicts
        self.tz = tz
        super().__init__(format_conflicts(conflicts, tz))


def format_conflicts(conflicts: list[ScheduleConflict], tz: str) -> str:
    lines = [
        ru.EVENT_CONFLICT_LINE.format(
            title=html.escape(c.title), when=format_interval(c.starts_at, c.ends_at, tz)
        )
        for c in conflicts
    ]
    return "\n".join([ru.EVENT_CONFLICT_HEADER, *lines])


def format_tournament_period(
    starts_at: datetime | None, duration_days: int | None, is_season: bool, tz: str
) -> str:
    """Даты турнира: сезон или дни; дата старта может быть ещё неизвестна."""
    start = to_local(starts_at, tz).date() if starts_at is not None else None
    if is_season:
        return (
            ru.PERIOD_SEASON_FROM.format(start=f"{start:%d.%m.%Y}") if start else ru.PERIOD_SEASON
        )
    days = duration_days or 1
    if start is None:
        return ru.PERIOD_DAYS_TBD.format(days=days)
    if days == 1:
        return f"{start:%d.%m.%Y}"
    end = start + timedelta(days=days - 1)
    return ru.PERIOD_DAYS.format(start=f"{start:%d.%m.%Y}", end=f"{end:%d.%m.%Y}", days=days)


def format_when(event: EventDTO, tz: str) -> str:
    """Когда: интервал события; у турнира — даты (сезон, дни или «уточняется»)."""
    if event.type == EventType.TOURNAMENT or event.starts_at is None or event.ends_at is None:
        return format_tournament_period(event.starts_at, event.duration_days, event.is_season, tz)
    return format_interval(event.starts_at, event.ends_at, tz)


def format_short(event: EventDTO, tz: str) -> str:
    """Коротко для кнопок: «дд.мм чч:мм»; у турнира — его даты."""
    if event.type == EventType.TOURNAMENT or event.starts_at is None:
        return format_when(event, tz)
    return to_local(event.starts_at, tz).strftime("%d.%m %H:%M")


def _tournament_line(event: EventDTO) -> str:
    if not event.tournament_title:
        return ""
    return "\n" + ru.EVENT_TOURNAMENT.format(title=html.escape(event.tournament_title))


def format_event_line(event: EventDTO, tz: str, squad_name: str | None = None) -> str:
    """Карточка события — как пост о нём, без описания. squad_name — строка «👥 состав»
    (когда событий нескольких составов)."""
    return ru.EVENT_CARD.format(
        emoji=EVENT_TYPE_EMOJI[event.type],
        type=EVENT_TYPE_TITLES[event.type],
        title=html.escape(event.title),
        when=format_when(event, tz),
        place=f"\n📍 {html.escape(event.place)}" if event.place else "",
        tournament=_tournament_line(event),
        squad=f"\n👥 {html.escape(squad_name)}" if squad_name else "",
    )


def format_announcement(event: EventDTO, tz: str) -> str:
    return ru.EVENT_ANNOUNCE.format(
        emoji=EVENT_TYPE_EMOJI[event.type],
        type=EVENT_TYPE_TITLES[event.type],
        title=html.escape(event.title),
        when=format_when(event, tz),
        place=f"\n📍 {html.escape(event.place)}" if event.place else "",
        tournament=_tournament_line(event),
        description=f"\n\n{html.escape(event.description)}" if event.description else "",
    )


def format_reminder(event: EventDTO, offset_days: int, tz: str) -> str:
    when_left = ru.EVENT_IN_DAYS.get(offset_days) or ru.EVENT_IN_N_DAYS.format(days=offset_days)
    return ru.EVENT_REMINDER.format(
        when_left=when_left,
        title=html.escape(event.title),
        when=format_when(event, tz),
        place=f"\n📍 {html.escape(event.place)}" if event.place else "",
        tournament=_tournament_line(event),
        description=f"\n{html.escape(event.description)}" if event.description else "",
    )


async def to_dtos(uow: UnitOfWork, events: Sequence[Event]) -> list[EventDTO]:
    """DTO событий с названием турнира у матчей, привязанных к турниру."""
    titles = await uow.events.titles(sorted({e.tournament_id for e in events if e.tournament_id}))
    return [
        EventDTO.model_validate(e).model_copy(
            update={"tournament_title": titles.get(e.tournament_id) if e.tournament_id else None}
        )
        for e in events
    ]


async def collect_conflicts(
    uow: UnitOfWork, squad_id: int, starts_at: datetime, ends_at: datetime
) -> list[ScheduleConflict]:
    """Тренировки, матчи и события состава, пересекающиеся по времени с [starts_at, ends_at)."""
    window_start = starts_at - CONFLICT_LOOKBACK
    found: list[ScheduleConflict] = []
    for training in await uow.trainings.list_between(window_start, ends_at, squad_id=squad_id):
        end = training.starts_at + timedelta(minutes=training.duration_minutes)
        found.append(
            ScheduleConflict(
                kind="training",
                title=ru.CONFLICT_TRAINING.format(venue=training.venue),
                starts_at=training.starts_at,
                ends_at=end,
            )
        )
    for match in await uow.matches.list_between(squad_id, window_start, ends_at):
        found.append(
            ScheduleConflict(
                kind="match",
                title=ru.CONFLICT_MATCH.format(opponent=match.opponent),
                starts_at=match.starts_at,
                ends_at=match.starts_at + timedelta(minutes=MATCH_DURATION_MINUTES),
            )
        )
    for event in await uow.events.list_between(window_start, ends_at, [squad_id]):
        # матч из ФТ-4 уже учтён выше; турнир — период (дни, сезон), а заняты в нём — матчи
        if event.match_id is not None or event.type == EventType.TOURNAMENT:
            continue
        if event.starts_at is None:
            continue
        found.append(
            ScheduleConflict(
                kind="event",
                title=ru.CONFLICT_EVENT.format(
                    type=EVENT_TYPE_TITLES[event.type], title=event.title
                ),
                starts_at=event.starts_at,
                ends_at=event.starts_at + timedelta(minutes=event.duration_minutes),
            )
        )
    conflicts = [c for c in found if overlaps(starts_at, ends_at, c.starts_at, c.ends_at)]
    return sorted(conflicts, key=lambda c: c.starts_at)


class EventService:
    def __init__(self, uow_factory: UowFactory, notifier: Notifier, recipients: Recipients) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier
        self._recipients = recipients

    @staticmethod
    def _require_for(actor: UserContext, squad_id: int, event_type: EventType) -> None:
        """Собрание — право «собрания», остальные события — «события»."""
        require(actor, squad_id, event_permission(event_type))

    async def find_conflicts(
        self, actor: UserContext, squad_id: int, starts_at: datetime, duration_minutes: int
    ) -> list[ScheduleConflict]:
        if not (actor.can(squad_id, Permission.EVENTS) or actor.can(squad_id, Permission.MEETINGS)):
            raise PermissionDenied(ru.NO_PERMISSION)
        async with self._uow_factory() as uow:
            ends_at = starts_at + timedelta(minutes=duration_minutes)
            return await collect_conflicts(uow, squad_id, starts_at, ends_at)

    async def create(
        self,
        actor: UserContext,
        data: EventCreate,
        *,
        confirm_conflicts: bool = False,
        now: datetime | None = None,
    ) -> EventDTO:
        """Создаёт событие. Пересечения разрешены только после явного подтверждения.
        Турнир — период (дни или сезон): пересечения не проверяются, старт может быть в прошлом
        (сезон уже идёт) или ещё неизвестен."""
        self._require_for(actor, data.squad_id, data.type)
        now = now or utcnow()
        async with self._uow_factory() as uow:
            tz = squad_tz(await uow.squads.get(data.squad_id))
            if data.tournament_id is not None:
                tournament = await self._get_tournament(uow, data.tournament_id)
                if tournament.squad_id != data.squad_id:
                    raise Conflict(ru.TOURNAMENT_OTHER_SQUAD)
            conflicts: list[ScheduleConflict] = []
            if data.type != EventType.TOURNAMENT:
                if data.starts_at is None or data.starts_at <= now:
                    raise Conflict(ru.PAST_DATE)
                ends_at = data.starts_at + timedelta(minutes=data.duration_minutes)
                conflicts = await collect_conflicts(uow, data.squad_id, data.starts_at, ends_at)
                if conflicts and not confirm_conflicts:
                    raise ScheduleConflictError(conflicts, tz)

            event = await uow.events.add(
                Event(**data.model_dump(), created_by_user_id=actor.user.id)
            )
            [dto] = await to_dtos(uow, [event])
            destination = await resolve_destination(uow, data.squad_id, event_topic(data.type))
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="event.create",
                entity="event",
                entity_id=event.id,
                payload={"conflicts": len(conflicts), "published": data.notify_group},
            )
            await uow.commit()
        if not data.notify_group:
            return dto
        # публикация — после commit: событие уже сохранено, сетевой вызов не держит транзакцию
        return await self._publish(dto, tz, destination)

    async def move(
        self,
        actor: UserContext,
        event_id: int,
        new_starts_at: datetime,
        *,
        now: datetime | None = None,
    ) -> EventDTO:
        """Перенос события (для турнира — даты старта). Команда получает новое сообщение
        о переносе, опрос участия публикуется заново (старый закрывается — время другое),
        напоминания пересчитываются по новой дате."""
        return await self._move(event_id, new_starts_at, actor=actor, now=now or utcnow())

    async def _move(
        self,
        event_id: int,
        new_starts_at: datetime,
        *,
        actor: UserContext | None,
        now: datetime,
        place: str | None = None,
    ) -> EventDTO:
        """actor None — перенос из источника матчей (права не проверяются); place — новое
        место, если оно тоже изменилось."""
        async with self._uow_factory() as uow:
            event = await uow.events.get(event_id, for_update=True)
            if event is None:
                raise NotFound("Событие не найдено")
            if actor is not None:
                self._require_for(actor, event.squad_id, event.type)
            is_tournament = event.type == EventType.TOURNAMENT
            if not is_tournament and (
                event.starts_at is None or event.starts_at <= now or new_starts_at <= now
            ):
                raise Conflict(ru.EVENT_MOVE_PAST)
            old = event.starts_at
            event.starts_at = new_starts_at
            if place is not None:
                event.place = place
            poll = await self._active_poll(uow, event)
            if poll is not None:
                await uow.polls.supersede(poll, now)
            old_pin = _take_pin(event)  # старый пост с прежним временем — открепляется
            await uow.system.release_reminders(f"event:{event.id}:")
            tz = squad_tz(await uow.squads.get(event.squad_id))
            [dto] = await to_dtos(uow, [event])
            destination = await resolve_destination(uow, event.squad_id, event_topic(event.type))
            await uow.system.audit(
                actor_user_id=actor.user.id if actor is not None else None,
                action="event.move",
                entity="event",
                entity_id=event.id,
                payload={
                    "from": old.isoformat() if old else None,
                    "to": new_starts_at.isoformat(),
                },
            )
            await uow.commit()
        if poll is not None and poll.closed_at is None:  # закрытый уже остановлен в Telegram
            await self._notifier.stop_poll(poll.chat_id, poll.message_id)
        if old_pin is not None:
            await self._notifier.unpin_message(*old_pin)
        if not dto.notify_group:
            return dto
        if destination is None:
            return dto.model_copy(update={"posted": False})
        old_dto = dto.model_copy(update={"starts_at": old})
        text = ru.EVENT_MOVED_GROUP.format(
            type=EVENT_TYPE_TITLES[dto.type],
            title=html.escape(dto.title),
            old=format_when(old_dto, tz),
            new=format_when(dto, tz),
            place=f"\n📍 {html.escape(dto.place)}" if dto.place else "",
            tournament=_tournament_line(dto),
        )
        if dto.type == EventType.REVIEW:  # у разбора закреплён опрос, сообщение о переносе — нет
            posted = (
                await self._notifier.send_message(
                    destination.chat_id, text, thread_id=destination.thread_id
                )
                is not None
            )
            return dto.model_copy(
                update={
                    "posted": posted,
                    "poll_posted": await self._publish_pinned_poll(dto, tz, destination),
                }
            )
        posted = await self._post_pinned(dto, destination, text)
        poll_posted = (
            await self._publish_poll(dto, tz, destination) is not None
            if dto.type in EVENT_POLLS
            else None
        )
        return dto.model_copy(update={"posted": posted, "poll_posted": poll_posted})

    async def _publish(self, dto: EventDTO, tz: str, destination: Destination | None) -> EventDTO:
        """Пост и опрос участия в группе. Результат — в posted / poll_posted, чтобы
        пользователь узнал, если что-то не дошло (бота удалили из группы и т.п.)."""
        if destination is None:
            return dto.model_copy(update={"posted": False})
        if dto.type == EventType.REVIEW:  # у разбора нет отдельного поста: всё — в опросе
            posted = await self._publish_pinned_poll(dto, tz, destination)
            return dto.model_copy(update={"posted": posted, "poll_posted": posted})
        posted = await self._post_pinned(dto, destination, format_announcement(dto, tz))
        poll_posted = (
            await self._publish_poll(dto, tz, destination) is not None
            if dto.type in EVENT_POLLS
            else None
        )
        return dto.model_copy(update={"posted": posted, "poll_posted": poll_posted})

    async def _publish_pinned_poll(self, dto: EventDTO, tz: str, destination: Destination) -> bool:
        """Опрос участия с тихим закрепом — вместо поста (разбор игры)."""
        message_id = await self._publish_poll(dto, tz, destination)
        if message_id is None:
            return False
        await self._pin(dto.id, destination.chat_id, message_id)
        return True

    async def _post_pinned(self, dto: EventDTO, destination: Destination, text: str) -> bool:
        """Пост о событии (анонс или перенос) — с тихим закрепом, чтобы информация о нём
        была под рукой, а не терялась за опросом участия. Закреп снимается при переносе,
        удалении и после окончания события (unpin_ended)."""
        message_id = await self._notifier.send_message(
            destination.chat_id, text, thread_id=destination.thread_id
        )
        if message_id is None:
            return False
        await self._pin(dto.id, destination.chat_id, message_id)
        return True

    async def _pin(self, event_id: int, chat_id: int, message_id: int) -> None:
        """Тихий закреп сообщения события; запоминается, чтобы снять его позже."""
        if await self._notifier.pin_message(chat_id, message_id):
            async with self._uow_factory() as uow:
                event = await uow.events.get(event_id)
                if event is not None:
                    event.pinned_chat_id = chat_id
                    event.pinned_message_id = message_id
                    await uow.commit()

    async def unpin_ended(self, *, now: datetime | None = None) -> int:
        """Тик: снимает закреп с постов о закончившихся событиях. Сезонный турнир (конец
        неизвестен) открепляется только при удалении."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            pins: list[tuple[int, int]] = []
            for event in await uow.events.list_pinned_started(now):
                [dto] = await to_dtos(uow, [event])
                if dto.ends_at is None or dto.ends_at > now:
                    continue
                pin = _take_pin(event)
                if pin is not None:
                    pins.append(pin)
            await uow.commit()  # отметка — до запросов в Telegram: сбой не приведёт к повтору
        for pin in pins:
            await self._notifier.unpin_message(*pin)
        if pins:
            log.info("event_pins_removed", count=len(pins))
        return len(pins)

    async def create_from_match(
        self,
        squad_id: int,
        match_id: int,
        title: str,
        starts_at: datetime,
        place: str | None,
        *,
        description: str | None = None,
        tournament_id: int | None = None,
    ) -> EventDTO:
        """Матч из источника (mtgame) — в журнал, с постом и опросом участия в топике match.
        Пересечения не проверяются: время назначает лига."""
        async with self._uow_factory() as uow:
            event = await uow.events.add(
                Event(
                    squad_id=squad_id,
                    type=EventType.MATCH,
                    title=title[:200],
                    starts_at=starts_at,
                    duration_minutes=MATCH_DURATION_MINUTES,
                    place=place,
                    description=description,
                    notify_group=True,
                    match_id=match_id,
                    tournament_id=tournament_id,
                )
            )
            [dto] = await to_dtos(uow, [event])
            tz = squad_tz(await uow.squads.get(squad_id))
            destination = await resolve_destination(uow, squad_id, event_topic(EventType.MATCH))
            await uow.system.audit(
                actor_user_id=None,
                action="event.create",
                entity="event",
                entity_id=event.id,
                payload={"match_id": match_id, "published": True},
            )
            await uow.commit()
        return await self._publish(dto, tz, destination)

    async def update_from_match(
        self, event_id: int, starts_at: datetime, place: str | None, *, now: datetime | None = None
    ) -> EventDTO | None:
        """Матч изменился в источнике. Другое время — перенос, как кнопкой в /events (сообщение
        и новый опрос); только другое место — сообщение в топике. Прошедшее событие и
        событие без изменений не трогаются (None)."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            event = await uow.events.get(event_id)
            if event is None or event.starts_at is None or event.starts_at <= now:
                return None
            moved = event.starts_at != starts_at
            if not moved and (place is None or event.place == place):
                return None
        if moved:
            return await self._move(event_id, starts_at, actor=None, now=now, place=place)
        async with self._uow_factory() as uow:
            event = await uow.events.get(event_id)
            if event is None:
                return None
            event.place = place
            [dto] = await to_dtos(uow, [event])
            tz = squad_tz(await uow.squads.get(event.squad_id))
            destination = await resolve_destination(uow, event.squad_id, event_topic(dto.type))
            await uow.commit()
        if dto.notify_group and destination is not None and place is not None:
            text = ru.EVENT_PLACE_CHANGED.format(
                type=EVENT_TYPE_TITLES[dto.type],
                title=html.escape(dto.title),
                when=format_when(dto, tz),
                place=html.escape(place),
            )
            await self._notifier.send_message(
                destination.chat_id, text, thread_id=destination.thread_id
            )
        return dto

    async def delete(
        self, actor: UserContext, event_id: int, *, now: datetime | None = None
    ) -> None:
        """Удаляет событие. Если оно ещё не прошло — команда получает новое сообщение об отмене:
        в группу (если событие публиковалось) и тренерам/капитанам в личку."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            event = await uow.events.get(event_id)
            if event is None:
                raise NotFound("Событие не найдено")
            self._require_for(actor, event.squad_id, event.type)
            event.deleted_at = now
            polls = [await self._active_poll(uow, event)]
            payment = await uow.game_payments.active_for_event(event.id)
            if payment is not None:  # игру удалили — сбор денег за неё отменяется
                payment.cancelled_at = now
                polls.append(
                    await uow.polls.get_for_ref(PollType.GAME_PAYMENT, GAME_PAYMENT_REF, payment.id)
                )
            for poll in polls:
                if poll is not None:
                    await uow.polls.supersede(poll, now)
            pin = _take_pin(event)
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="event.delete",
                entity="event",
                entity_id=event_id,
            )
            await uow.commit()
            for poll in polls:
                if poll is not None and poll.closed_at is None:
                    await self._notifier.stop_poll(poll.chat_id, poll.message_id)
            if pin is not None:
                await self._notifier.unpin_message(*pin)
            [dto] = await to_dtos(uow, [event])
            if _is_over(dto, now):
                return
            text = ru.EVENT_CANCELLED.format(
                type=EVENT_TYPE_TITLES[dto.type],
                title=html.escape(dto.title),
                when=format_when(dto, squad_tz(await uow.squads.get(dto.squad_id))),
                tournament=_tournament_line(dto),
            )
            if event.notify_group:
                await send_to_squad(uow, self._notifier, dto.squad_id, event_topic(dto.type), text)
            await self._recipients.notify(
                uow, self._notifier, dto.squad_id, event_permission(dto.type), text
            )
            await uow.commit()  # dm_available

    async def _publish_poll(self, event: EventDTO, tz: str, destination: Destination) -> int | None:
        """Опрос участия сразу после поста — для матча и турнира; у разбора игры поста нет,
        опрос — единственное сообщение (с интервалом и местом). Если Telegram опрос не принял,
        событие всё равно создаётся. Возвращает message_id опроса."""
        spec = EVENT_POLLS.get(event.type)
        if spec is None:
            return None
        if event.type == EventType.REVIEW:
            when = format_when(event, tz) + (f", 📍 {event.place}" if event.place else "")
        elif event.type != EventType.TOURNAMENT and event.starts_at is not None:
            when = format_local(event.starts_at, tz)
        else:
            when = format_when(event, tz)
        tournament = (
            ru.EVENT_POLL_TOURNAMENT.format(title=event.tournament_title)
            if event.tournament_title
            else ""
        )
        question = spec.question.format(
            type=EVENT_TYPE_TITLES[event.type],
            title=event.title,
            tournament=tournament,
            when=when,
        )[:300]
        sent = await self._notifier.send_poll(
            destination.chat_id, question, spec.options, thread_id=destination.thread_id
        )
        if sent is None:
            log.warning("event_poll_not_sent", event_id=event.id)
            return None
        async with self._uow_factory() as uow:
            await uow.polls.add(
                Poll(
                    type=spec.poll_type,
                    squad_id=event.squad_id,
                    ref_type=EVENT_REF,
                    ref_id=event.id,
                    tg_poll_id=sent.poll_id,
                    chat_id=destination.chat_id,
                    message_id=sent.message_id,
                    question=question,
                    options=list(spec.options),
                    closes_at=event.starts_at,
                )
            )
            await uow.commit()
        return sent.message_id

    @staticmethod
    async def _active_poll(uow: UnitOfWork, event: Event | EventDTO) -> Poll | None:
        spec = EVENT_POLLS.get(event.type)
        if spec is None:
            return None
        return await uow.polls.get_for_ref(spec.poll_type, EVENT_REF, event.id)

    async def review_default_at(
        self, squad_id: int, *, now: datetime | None = None
    ) -> datetime | None:
        """Время разбора игры по умолчанию: за час до первой тренировки недели."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            tz = squad_tz(await uow.squads.get(squad_id))
            today = to_local(now, tz).date()
            week_start = local_to_utc(
                today - timedelta(days=today.weekday()), datetime.min.time(), tz
            )
            trainings = await uow.trainings.list_between(
                week_start, now + timedelta(days=14), squad_id=squad_id
            )
            return review_default_at(
                [t.starts_at for t in trainings], tz=tz, now=now, lead=REVIEW_LEAD
            )

    async def with_polls(self, squad_id: int, *, now: datetime | None = None) -> list[EventDTO]:
        """События состава с опросом участия: от недели назад до двух месяцев вперёд."""
        now = now or utcnow()
        back, ahead = TOURNAMENTS_WINDOW
        async with self._uow_factory() as uow:
            events = await uow.events.list_between(
                now - timedelta(days=7), now + timedelta(days=60), [squad_id]
            )
            # турниры — и без даты старта, и сезонные, начавшиеся давно
            tournaments = await uow.events.list_of_type(
                EventType.TOURNAMENT, now - back, now + ahead, [squad_id]
            )
            known = {e.id for e in events}
            events += [t for t in tournaments if t.id not in known]
            with_poll = [
                e
                for e in events
                if e.type in EVENT_POLLS and await self._active_poll(uow, e) is not None
            ]
            return await to_dtos(uow, with_poll)

    # --- турниры: связь с матчами настраивает админ ---
    async def tournaments(
        self, actor: UserContext, *, now: datetime | None = None
    ) -> list[tuple[EventDTO, str, str]]:
        """Турниры всех составов (полгода назад — год вперёд):
        (турнир, название состава, часовой пояс)."""
        if not actor.can_anywhere(Permission.EVENTS):
            raise PermissionDenied(ru.NO_PERMISSION)
        now = now or utcnow()
        back, ahead = TOURNAMENTS_WINDOW
        # админ — турниры всех составов; остальные — составов, где есть право
        squad_ids = None if actor.is_admin else actor.squads_with(Permission.EVENTS)
        async with self._uow_factory() as uow:
            events = await uow.events.list_of_type(
                EventType.TOURNAMENT, now - back, now + ahead, squad_ids
            )
            squads = {
                s.id: s for s in await uow.squads.list_by_ids(sorted({e.squad_id for e in events}))
            }
            return [
                (dto, squads[dto.squad_id].name, squad_tz(squads[dto.squad_id]))
                for dto in await to_dtos(uow, events)
                if dto.squad_id in squads
            ]

    async def squad_tournaments(
        self, actor: UserContext, squad_id: int, *, now: datetime | None = None
    ) -> list[EventDTO]:
        """Незакончившиеся турниры состава — для выбора при создании матча."""
        require(actor, squad_id, Permission.EVENTS)
        now = now or utcnow()
        back, ahead = TOURNAMENTS_WINDOW
        async with self._uow_factory() as uow:
            events = await uow.events.list_of_type(
                EventType.TOURNAMENT, now - back, now + ahead, [squad_id]
            )
            dtos = [EventDTO.model_validate(e) for e in events]
            return [t for t in dtos if not _is_over(t, now)]

    async def tournament_card(
        self, actor: UserContext, tournament_id: int, *, now: datetime | None = None
    ) -> TournamentCard:
        now = now or utcnow()
        async with self._uow_factory() as uow:
            tournament = await self._get_tournament(uow, tournament_id)
            require(actor, tournament.squad_id, Permission.EVENTS)
            squad = await uow.squads.get(tournament.squad_id)
            matches = await uow.events.list_tournament_candidates(
                tournament, now - TOURNAMENT_MATCHES_LOOKBACK
            )
            return TournamentCard(
                tournament=EventDTO.model_validate(tournament),
                squad_name=squad.name if squad else "",
                tz=squad_tz(squad),
                matches=[EventDTO.model_validate(m) for m in matches],
            )

    async def link_match(
        self, actor: UserContext, tournament_id: int, match_event_id: int, *, linked: bool
    ) -> None:
        """Привязывает матч к турниру (linked) или отвязывает. Матч — того же состава;
        у матча может быть только один турнир."""
        async with self._uow_factory() as uow:
            tournament = await self._get_tournament(uow, tournament_id)
            require(actor, tournament.squad_id, Permission.EVENTS)
            match = await uow.events.get(match_event_id)
            if match is None or match.type != EventType.MATCH:
                raise NotFound(ru.TOURNAMENT_MATCH_NOT_FOUND)
            if match.squad_id != tournament.squad_id:
                raise Conflict(ru.TOURNAMENT_OTHER_SQUAD)
            if linked:
                if match.tournament_id not in (None, tournament.id):
                    raise Conflict(ru.TOURNAMENT_MATCH_TAKEN)
                match.tournament_id = tournament.id
            elif match.tournament_id == tournament.id:
                match.tournament_id = None
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="tournament.link" if linked else "tournament.unlink",
                entity="event",
                entity_id=match.id,
                payload={"tournament_id": tournament.id},
            )
            await uow.commit()

    async def set_game_price(
        self, actor: UserContext, tournament_id: int, price: Decimal | None
    ) -> EventDTO:
        """Стоимость одной игры турнира (вся сумма); None — не указана."""
        return await self._update_tournament(actor, tournament_id, game_price=price)

    async def rename_tournament(
        self, actor: UserContext, tournament_id: int, title: str
    ) -> EventDTO:
        title = title.strip()
        if not 1 <= len(title) <= TITLE_MAX:
            raise Conflict(ru.TOURNAMENT_BAD_TITLE)
        return await self._update_tournament(actor, tournament_id, title=title)

    async def set_division(
        self, actor: UserContext, tournament_id: int, division: str | None
    ) -> EventDTO:
        """Дивизион лиги (ABL «Москвич»); None — не указан."""
        division = division.strip() if division else None
        if division is not None and len(division) > DIVISION_MAX:
            raise Conflict(ru.TOURNAMENT_BAD_DIVISION)
        return await self._update_tournament(actor, tournament_id, division=division or None)

    async def _update_tournament(
        self, actor: UserContext, tournament_id: int, **fields: str | Decimal | None
    ) -> EventDTO:
        async with self._uow_factory() as uow:
            tournament = await self._get_tournament(uow, tournament_id)
            require(actor, tournament.squad_id, Permission.EVENTS)
            for name, value in fields.items():
                setattr(tournament, name, value)
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="tournament.update",
                entity="event",
                entity_id=tournament.id,
                payload={k: str(v) if v is not None else None for k, v in fields.items()},
            )
            await uow.commit()
            return EventDTO.model_validate(tournament)

    @staticmethod
    async def _get_tournament(uow: UnitOfWork, tournament_id: int) -> Event:
        tournament = await uow.events.get(tournament_id)
        if tournament is None or tournament.type != EventType.TOURNAMENT:
            raise NotFound(ru.TOURNAMENT_NOT_FOUND)
        return tournament

    async def get(self, event_id: int) -> EventDTO:
        async with self._uow_factory() as uow:
            event = await uow.events.get(event_id)
            if event is None:
                raise NotFound("Событие не найдено")
            [dto] = await to_dtos(uow, [event])
            return dto

    async def upcoming(
        self, squad_ids: list[int], *, now: datetime | None = None, days: int = 60
    ) -> list[tuple[EventDTO, str, str]]:
        """Ближайшие события: (событие, название состава, часовой пояс)."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            events = await uow.events.list_between(now, now + timedelta(days=days), squad_ids)
            squads = {
                s.id: s for s in await uow.squads.list_by_ids(sorted({e.squad_id for e in events}))
            }
            return [
                (dto, squads[dto.squad_id].name, squad_tz(squads[dto.squad_id]))
                for dto in await to_dtos(uow, events)
                if dto.squad_id in squads
            ]

    async def send_due_reminders(self, *, now: datetime | None = None) -> int:
        """Напоминания тренерам и капитанам за 3, 1 день и в день события (настраивается)."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            event_ids = [e.id for e in await uow.events.list_between(now, now + REMINDER_HORIZON)]
        sent = 0
        for event_id in event_ids:
            async with self._uow_factory() as uow:
                event = await uow.events.get(event_id)
                if event is None or event.starts_at is None:
                    continue
                squad = await uow.squads.get(event.squad_id)
                settings = await load_squad_settings(uow, event.squad_id)
                if squad is None or not settings.features.event_reminders:
                    continue
                [dto] = await to_dtos(uow, [event])
                for offset in due_reminder_offsets(
                    starts_at=event.starts_at,
                    tz=squad.timezone,
                    offsets_days=settings.event_reminder_days,
                    reminder_time=settings.event_reminder_time,
                    now=now,
                    grace=REMINDER_GRACE,
                ):
                    if not await uow.system.claim_reminder(f"event:{event.id}:{offset}", "event"):
                        continue
                    await uow.commit()  # отметка — до рассылки: сбой не приведёт к повтору
                    text = format_reminder(dto, offset, squad.timezone)
                    await self._recipients.notify(
                        uow, self._notifier, event.squad_id, event_permission(event.type), text
                    )
                    if event.notify_group:
                        await send_to_squad(
                            uow, self._notifier, event.squad_id, event_topic(event.type), text
                        )
                    sent += 1
                await uow.commit()
        log.info("event_reminders_sent", count=sent)
        return sent

    # --- разбор игры: предложение тренеру после матча (ФТ-5) ---
    async def offer_reviews(self, *, now: datetime | None = None) -> int:
        """Матч закончился — тренерам состава (нет тренера — капитанам) в личку: «Назначить
        разбор?». Каждому матчу — один раз; отметка — до рассылки."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            match_ids = [
                m.id
                for m in await uow.events.list_of_type(
                    EventType.MATCH, now - REVIEW_OFFER_WINDOW, now
                )
            ]
        offered = 0
        for match_id in match_ids:
            async with self._uow_factory() as uow:
                match = await uow.events.get(match_id)
                if match is None or match.starts_at is None:
                    continue
                if match.starts_at + timedelta(minutes=match.duration_minutes) > now:
                    continue  # матч ещё идёт
                settings = await load_squad_settings(uow, match.squad_id)
                squad = await uow.squads.get(match.squad_id)
                if squad is None or not settings.features.review_offers:
                    continue
                if not await uow.system.claim_reminder(review_offer_key(match.id), "review"):
                    continue
                await uow.commit()
                [dto] = await to_dtos(uow, [match])
                text = ru.REVIEW_OFFER.format(
                    match=format_event_line(dto, squad.timezone, squad.name)
                )
                keyboard = [
                    [
                        InlineButton(
                            ru.REVIEW_BTN_YES,
                            callbacks.pack(callbacks.REVIEW, callbacks.REVIEW_YES, match.id),
                        )
                    ],
                    [
                        InlineButton(
                            ru.REVIEW_BTN_NO,
                            callbacks.pack(callbacks.REVIEW, callbacks.REVIEW_NO, match.id),
                        )
                    ],
                ]
                await self._recipients.notify(
                    uow, self._notifier, match.squad_id, Permission.EVENTS, text, keyboard=keyboard
                )
                await uow.commit()  # dm_available
                offered += 1
        return offered

    async def review_proposal(
        self, actor: UserContext, match_event_id: int, *, now: datetime | None = None
    ) -> ReviewProposal:
        """Тренер согласился на разбор — время по умолчанию для подтверждения."""
        async with self._uow_factory() as uow:
            match = await self._get_match(uow, match_event_id)
            require(actor, match.squad_id, Permission.EVENTS)
            if await uow.system.count_reminders(review_key(match.id)):
                raise Conflict(ru.REVIEW_ALREADY)
            [dto] = await to_dtos(uow, [match])
            tz = squad_tz(await uow.squads.get(match.squad_id))
        default_at = await self.review_default_at(match.squad_id, now=now)
        return ReviewProposal(match=dto, tz=tz, default_at=default_at)

    async def create_review(
        self,
        actor: UserContext,
        match_event_id: int,
        starts_at: datetime,
        *,
        now: datetime | None = None,
    ) -> EventDTO:
        """Разбор игры по матчу: событие с закреплённым опросом «Буду / Не смогу» в группе.
        По одному матчу — один разбор (повторное нажатие, второй тренер — отказ)."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            match = await self._get_match(uow, match_event_id)
            require(actor, match.squad_id, Permission.EVENTS)
            if starts_at <= now:
                raise Conflict(ru.PAST_DATE)
            if not await uow.system.claim_reminder(review_key(match.id), "review"):
                raise Conflict(ru.REVIEW_ALREADY)
            await uow.commit()
            squad_id, title = match.squad_id, match.title
        try:
            return await self.create(
                actor,
                EventCreate(
                    squad_id=squad_id,
                    type=EventType.REVIEW,
                    title=title[:200],  # тип «Разбор игры» добавляют шаблоны
                    starts_at=starts_at,
                    duration_minutes=REVIEW_DURATION_MINUTES,
                    notify_group=True,
                ),
                confirm_conflicts=True,  # время выбрал тренер; разбор — обычно у зала
                now=now,
            )
        except Exception:
            async with self._uow_factory() as uow:
                await uow.system.release_reminder(review_key(match_event_id))
                await uow.commit()
            raise

    @staticmethod
    async def _get_match(uow: UnitOfWork, match_event_id: int) -> Event:
        match = await uow.events.get(match_event_id)
        if match is None or match.type != EventType.MATCH:
            raise NotFound(ru.TOURNAMENT_MATCH_NOT_FOUND)
        return match


def _is_over(event: EventDTO, now: datetime) -> bool:
    """Событие уже прошло (у турнира — закончилось): сообщение об отмене не нужно."""
    if event.type == EventType.TOURNAMENT:
        return event.ends_at is not None and event.ends_at <= now
    return event.starts_at is not None and event.starts_at <= now


def _take_pin(event: Event) -> tuple[int, int] | None:
    """Забирает закреп события (chat_id, message_id) и обнуляет его — вызывается в транзакции,
    открепление в Telegram — после commit."""
    if event.pinned_chat_id is None or event.pinned_message_id is None:
        return None
    pin = (event.pinned_chat_id, event.pinned_message_id)
    event.pinned_chat_id = event.pinned_message_id = None
    return pin
