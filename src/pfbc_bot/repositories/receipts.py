from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import ReceiptRequest


class ReceiptRequestsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, request: ReceiptRequest) -> ReceiptRequest:
        """Запись с flush: второй опрос за месяц — IntegrityError (уникальность состав+месяц)."""
        self._session.add(request)
        await self._session.flush()
        return request

    async def get(self, request_id: int) -> ReceiptRequest | None:
        return await self._session.get(ReceiptRequest, request_id)

    async def for_month(self, squad_id: int, month: date) -> ReceiptRequest | None:
        stmt = select(ReceiptRequest).where(
            ReceiptRequest.squad_id == squad_id, ReceiptRequest.month == month
        )
        return (await self._session.scalars(stmt)).first()

    async def list_due(self, since: datetime, until: datetime) -> list[ReceiptRequest]:
        """Опросы со сроком в [since, until]."""
        stmt = select(ReceiptRequest).where(
            ReceiptRequest.deadline_at >= since, ReceiptRequest.deadline_at <= until
        )
        return list(await self._session.scalars(stmt.order_by(ReceiptRequest.deadline_at)))
