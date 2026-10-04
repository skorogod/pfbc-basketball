from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import Match


class MatchesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_between(self, squad_id: int, start: datetime, end: datetime) -> list[Match]:
        stmt = select(Match).where(
            Match.squad_id == squad_id, Match.starts_at >= start, Match.starts_at < end
        )
        return list(await self._session.scalars(stmt.order_by(Match.starts_at)))
