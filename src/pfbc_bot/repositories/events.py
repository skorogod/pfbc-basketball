from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import Event


class EventsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: Event) -> Event:
        self._session.add(event)
        await self._session.flush()
        return event

    async def get(self, event_id: int) -> Event | None:
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
