"""Журнал событий состава: создание с проверкой пересечений, публикация, напоминания (ФТ-9)."""

import html
from datetime import datetime, timedelta

from pfbc_bot.db.models import Event
from pfbc_bot.domain.dates import due_reminder_offsets, format_interval, overlaps, utcnow
from pfbc_bot.domain.enums import (
    EVENT_TYPE_TITLES,
    MATCH_DURATION_MINUTES,
    EventType,
    SquadRole,
    TopicPurpose,
)
from pfbc_bot.logging import get_logger
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.events import EventCreate, EventDTO, ScheduleConflict
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.common import notify_staff, send_to_squad, squad_tz
from pfbc_bot.services.errors import Conflict, NotFound, PermissionDenied
from pfbc_bot.services.ports import Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)

EVENT_MANAGERS = (SquadRole.COACH, SquadRole.CAPTAIN)
REMINDER_HORIZON = timedelta(days=31)
REMINDER_GRACE = timedelta(hours=12)
# Самое длинное мероприятие — сутки: всё, что началось раньше, с новым событием не пересечётся.
CONFLICT_LOOKBACK = timedelta(days=1)
GAME_EVENT_TYPES = frozenset({EventType.MATCH, EventType.TOURNAMENT})


def event_topic(event_type: EventType) -> TopicPurpose:
    """Топик группы для поста и напоминаний: игровые события — в «Игры», остальные — в общий."""
    return TopicPurpose.GAMES if event_type in GAME_EVENT_TYPES else TopicPurpose.GENERAL


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


def format_event_line(event: EventDTO, squad_name: str, tz: str) -> str:
    place = f", {html.escape(event.place)}" if event.place else ""
    return ru.EVENT_LINE.format(
        when=format_interval(event.starts_at, event.ends_at, tz),
        title=html.escape(event.title),
        type=EVENT_TYPE_TITLES[event.type],
        squad=html.escape(squad_name),
        place=place,
    )


def format_announcement(event: EventDTO, tz: str) -> str:
    return ru.EVENT_ANNOUNCE.format(
        type=EVENT_TYPE_TITLES[event.type],
        title=html.escape(event.title),
        when=format_interval(event.starts_at, event.ends_at, tz),
        place=f"\n📍 {html.escape(event.place)}" if event.place else "",
        description=f"\n\n{html.escape(event.description)}" if event.description else "",
    )


def format_reminder(event: EventDTO, offset_days: int, tz: str) -> str:
    when_left = ru.EVENT_IN_DAYS.get(offset_days) or ru.EVENT_IN_N_DAYS.format(days=offset_days)
    return ru.EVENT_REMINDER.format(
        when_left=when_left,
        title=html.escape(event.title),
        when=format_interval(event.starts_at, event.ends_at, tz),
        place=f"\n📍 {html.escape(event.place)}" if event.place else "",
        description=f"\n{html.escape(event.description)}" if event.description else "",
    )


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
        if event.match_id is not None:
            continue  # матч уже учтён выше
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
    def __init__(self, uow_factory: UowFactory, notifier: Notifier) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier

    @staticmethod
    def _require_manager(actor: UserContext, squad_id: int) -> None:
        if not actor.has_role(squad_id, *EVENT_MANAGERS):
            raise PermissionDenied(ru.NO_PERMISSION)

    async def find_conflicts(
        self, actor: UserContext, squad_id: int, starts_at: datetime, duration_minutes: int
    ) -> list[ScheduleConflict]:
        self._require_manager(actor, squad_id)
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
        """Создаёт событие. Пересечения разрешены только после явного подтверждения."""
        self._require_manager(actor, data.squad_id)
        if data.starts_at <= (now or utcnow()):
            raise Conflict(ru.PAST_DATE)
        async with self._uow_factory() as uow:
            tz = squad_tz(await uow.squads.get(data.squad_id))
            ends_at = data.starts_at + timedelta(minutes=data.duration_minutes)
            conflicts = await collect_conflicts(uow, data.squad_id, data.starts_at, ends_at)
            if conflicts and not confirm_conflicts:
                raise ScheduleConflictError(conflicts, tz)

            event = await uow.events.add(
                Event(**data.model_dump(), created_by_user_id=actor.user.id)
            )
            dto = EventDTO.model_validate(event)
            if data.notify_group:
                await send_to_squad(
                    uow,
                    self._notifier,
                    data.squad_id,
                    event_topic(data.type),
                    format_announcement(dto, tz),
                )
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="event.create",
                entity="event",
                entity_id=event.id,
                payload={"conflicts": len(conflicts), "published": data.notify_group},
            )
            await uow.commit()
            return dto

    async def create_from_match(
        self,
        squad_id: int,
        match_id: int,
        title: str,
        starts_at: datetime,
        place: str | None,
    ) -> EventDTO:
        """Матчи ABL автоматически попадают в журнал (используется модулем матчей, этап 2)."""
        async with self._uow_factory() as uow:
            event = await uow.events.get_by_match(match_id)
            if event is None:
                event = await uow.events.add(
                    Event(
                        squad_id=squad_id,
                        type=EventType.MATCH,
                        title=title,
                        starts_at=starts_at,
                        duration_minutes=MATCH_DURATION_MINUTES,
                        place=place,
                        match_id=match_id,
                    )
                )
            else:
                event.title, event.starts_at, event.place = title, starts_at, place
            await uow.commit()
            return EventDTO.model_validate(event)

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
            self._require_manager(actor, event.squad_id)
            event.deleted_at = now
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="event.delete",
                entity="event",
                entity_id=event_id,
            )
            await uow.commit()
            if event.starts_at <= now:
                return
            dto = EventDTO.model_validate(event)
            text = ru.EVENT_CANCELLED.format(
                type=EVENT_TYPE_TITLES[dto.type],
                title=html.escape(dto.title),
                when=format_interval(
                    dto.starts_at, dto.ends_at, squad_tz(await uow.squads.get(dto.squad_id))
                ),
            )
            if event.notify_group:
                await send_to_squad(uow, self._notifier, dto.squad_id, event_topic(dto.type), text)
            await notify_staff(uow, self._notifier, dto.squad_id, text, roles=EVENT_MANAGERS)
            await uow.commit()  # dm_available

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
                (EventDTO.model_validate(e), squads[e.squad_id].name, squad_tz(squads[e.squad_id]))
                for e in events
                if e.squad_id in squads
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
                if event is None:
                    continue
                squad = await uow.squads.get(event.squad_id)
                settings = await load_squad_settings(uow, event.squad_id)
                if squad is None or not settings.features.event_reminders:
                    continue
                dto = EventDTO.model_validate(event)
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
                    await notify_staff(
                        uow, self._notifier, event.squad_id, text, roles=EVENT_MANAGERS
                    )
                    if event.notify_group:
                        await send_to_squad(
                            uow, self._notifier, event.squad_id, event_topic(event.type), text
                        )
                    sent += 1
                await uow.commit()
        log.info("event_reminders_sent", count=sent)
        return sent
