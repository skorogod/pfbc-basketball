from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import Event
from pfbc_bot.domain.enums import EventType


class EventsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: Event) -> Event:
        self._session.add(event)
        await self._session.flush()
        return event

    async def get(self, event_id: int, *, for_update: bool = False) -> Event | None:
        """for_update — блокировка строки до конца транзакции (сбор денег, перенос)."""
        if for_update:
            stmt = select(Event).where(Event.id == event_id).with_for_update()
            event = (await self._session.scalars(stmt)).first()
        else:
            event = await self._session.get(Event, event_id)
        return event if event and event.deleted_at is None else None

    async def get_by_match(self, match_id: int) -> Event | None:
        return (
            await self._session.scalars(select(Event).where(Event.match_id == match_id))
        ).first()

    async def list_between(
        self, start: datetime, end: datetime, squad_ids: Sequence[int] | None = None
    ) -> list[Event]:
        stmt = select(Event).where(
            Event.deleted_at.is_(None), Event.starts_at >= start, Event.starts_at < end
        )
        if squad_ids is not None:
            stmt = stmt.where(Event.squad_id.in_(list(squad_ids)))
        return list(await self._session.scalars(stmt.order_by(Event.starts_at)))

    async def list_of_type(
        self,
        event_type: EventType,
        start: datetime,
        end: datetime,
        squad_ids: Sequence[int] | None = None,
    ) -> list[Event]:
        """События типа со стартом в [start, end) и — для турниров — без даты старта."""
        stmt = select(Event).where(
            Event.deleted_at.is_(None),
            Event.type == event_type,
            or_(
                and_(Event.starts_at >= start, Event.starts_at < end),
                Event.starts_at.is_(None),
            ),
        )
        if squad_ids is not None:
            stmt = stmt.where(Event.squad_id.in_(list(squad_ids)))
        return list(await self._session.scalars(stmt.order_by(Event.starts_at.asc().nulls_last())))

    async def titles(self, event_ids: Sequence[int]) -> dict[int, str]:
        if not event_ids:
            return {}
        stmt = select(Event.id, Event.title).where(
            Event.id.in_(list(event_ids)), Event.deleted_at.is_(None)
        )
        return {row.id: row.title for row in await self._session.execute(stmt)}

    async def list_tournament_candidates(self, tournament: Event, since: datetime) -> list[Event]:
        """Матчи состава турнира: уже привязанные к нему и свободные, начиная с since."""
        stmt = select(Event).where(
            Event.deleted_at.is_(None),
            Event.squad_id == tournament.squad_id,
            Event.type == EventType.MATCH,
            (Event.tournament_id == tournament.id)
            | (Event.tournament_id.is_(None) & (Event.starts_at >= since)),
        )
        return list(await self._session.scalars(stmt.order_by(Event.starts_at)))
