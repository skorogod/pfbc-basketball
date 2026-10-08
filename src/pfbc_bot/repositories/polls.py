from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import Poll, PollAnswer
from pfbc_bot.domain.enums import PollType


class PollsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, poll: Poll) -> Poll:
        self._session.add(poll)
        await self._session.flush()
        return poll

    async def get_by_tg_id(self, tg_poll_id: str) -> Poll | None:
        return (
            await self._session.scalars(select(Poll).where(Poll.tg_poll_id == tg_poll_id))
        ).first()

    async def get_for_ref(self, poll_type: PollType, ref_type: str, ref_id: int) -> Poll | None:
        """Действующий опрос объекта (устаревшие после переноса не учитываются)."""
        stmt = select(Poll).where(
            Poll.type == poll_type,
            Poll.ref_type == ref_type,
            Poll.ref_id == ref_id,
            Poll.superseded_at.is_(None),
        )
        return (await self._session.scalars(stmt)).first()

    async def upsert_answer(
        self, poll_id: int, user_id: int, option_ids: list[int], answered_at: datetime
    ) -> None:
        stmt = insert(PollAnswer).values(
            poll_id=poll_id, user_id=user_id, option_ids=option_ids, answered_at=answered_at
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PollAnswer.poll_id, PollAnswer.user_id],
            set_={"option_ids": option_ids, "answered_at": answered_at},
        )
        await self._session.execute(stmt)

    async def answers(self, poll_id: int) -> dict[int, list[int]]:
        """user_id → выбранные варианты (пустые — голос отозван, не включаются)."""
        stmt = select(PollAnswer).where(PollAnswer.poll_id == poll_id)
        return {
            a.user_id: list(a.option_ids) for a in await self._session.scalars(stmt) if a.option_ids
        }
