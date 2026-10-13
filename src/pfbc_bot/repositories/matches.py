from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import Match
from pfbc_bot.domain.enums import MatchSource


class MatchesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, match: Match) -> Match:
        self._session.add(match)
        await self._session.flush()
        return match

    async def get_by_external(self, squad_id: int, external_id: str) -> Match | None:
        stmt = select(Match).where(Match.squad_id == squad_id, Match.external_id == external_id)
        return (await self._session.scalars(stmt)).first()

    async def has_upcoming(self, squad_id: int, now: datetime) -> bool:
        stmt = select(Match.id).where(Match.squad_id == squad_id, Match.starts_at > now).limit(1)
        return (await self._session.scalar(stmt)) is not None

    async def last_started(self, squad_id: int, now: datetime) -> datetime | None:
        """Начало последнего начавшегося (сыгранного) матча."""
        stmt = (
            select(Match.starts_at)
            .where(Match.squad_id == squad_id, Match.starts_at <= now)
            .order_by(Match.starts_at.desc())
            .limit(1)
        )
        last: datetime | None = await self._session.scalar(stmt)
        return last

    async def list_upcoming_from_source(self, squad_id: int, now: datetime) -> list[Match]:
        """Будущие матчи из источника, которые он ведёт (без ручного приоритета)."""
        stmt = select(Match).where(
            Match.squad_id == squad_id,
            Match.starts_at > now,
            Match.source == MatchSource.PARSER,
            Match.manual_override.is_(False),
        )
        return list(await self._session.scalars(stmt.order_by(Match.starts_at)))

    async def list_between(self, squad_id: int, start: datetime, end: datetime) -> list[Match]:
        stmt = select(Match).where(
            Match.squad_id == squad_id, Match.starts_at >= start, Match.starts_at < end
        )
        return list(await self._session.scalars(stmt.order_by(Match.starts_at)))
