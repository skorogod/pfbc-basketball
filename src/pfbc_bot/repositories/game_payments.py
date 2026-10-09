from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import GamePayment


class GamePaymentsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, payment: GamePayment) -> GamePayment:
        self._session.add(payment)
        await self._session.flush()
        return payment

    async def get(self, payment_id: int) -> GamePayment | None:
        return await self._session.get(GamePayment, payment_id)

    async def active_for_event(self, event_id: int) -> GamePayment | None:
        stmt = select(GamePayment).where(
            GamePayment.event_id == event_id, GamePayment.cancelled_at.is_(None)
        )
        return (await self._session.scalars(stmt.order_by(GamePayment.id.desc()))).first()

    async def list_due(self, since: datetime, until: datetime) -> list[GamePayment]:
        """Действующие сборы с дедлайном в [since, until]."""
        stmt = select(GamePayment).where(
            GamePayment.cancelled_at.is_(None),
            GamePayment.deadline_at >= since,
            GamePayment.deadline_at <= until,
        )
        return list(await self._session.scalars(stmt.order_by(GamePayment.deadline_at)))
