from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import GamePayment, GamePaymentPlayer, RosterNotice


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

    # --- заявка ---
    async def add_players(self, payment_id: int, user_ids: Iterable[int]) -> None:
        self._session.add_all(
            GamePaymentPlayer(payment_id=payment_id, user_id=uid) for uid in user_ids
        )
        await self._session.flush()

    async def player_ids(self, payment_id: int) -> set[int] | None:
        """Заявка сбора; None — сбор создан без заявки (до её появления)."""
        stmt = select(GamePaymentPlayer.user_id).where(GamePaymentPlayer.payment_id == payment_id)
        ids = set(await self._session.scalars(stmt))
        return ids or None

    # --- уведомления «отметились N игроков» ---
    async def add_notice(self, notice: RosterNotice) -> RosterNotice:
        self._session.add(notice)
        await self._session.flush()
        return notice

    async def notices(self, poll_id: int) -> list[RosterNotice]:
        """Уведомления по опросу участия — по одному на получателя."""
        stmt = select(RosterNotice).where(RosterNotice.poll_id == poll_id)
        return list(await self._session.scalars(stmt.order_by(RosterNotice.id)))

    async def visible_notices(
        self, *, poll_id: int | None = None, event_id: int | None = None
    ) -> list[RosterNotice]:
        """Уведомления с кнопкой «Начать сбор» (ещё не снятой); без фильтров — все."""
        stmt = select(RosterNotice).where(RosterNotice.hidden_at.is_(None))
        if poll_id is not None:
            stmt = stmt.where(RosterNotice.poll_id == poll_id)
        if event_id is not None:
            stmt = stmt.where(RosterNotice.event_id == event_id)
        return list(await self._session.scalars(stmt.order_by(RosterNotice.id)))
