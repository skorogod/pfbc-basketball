from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import Squad, SquadChat, SquadTopic
from pfbc_bot.domain.enums import TopicPurpose


class SquadsRepository:
    """Составы, их Telegram-группы и топики."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, squad_id: int) -> Squad | None:
        return await self._session.get(Squad, squad_id)

    async def get_by_name(self, name: str) -> Squad | None:
        return (await self._session.scalars(select(Squad).where(Squad.name == name))).first()

    async def add(self, squad: Squad) -> Squad:
        self._session.add(squad)
        await self._session.flush()
        return squad

    async def list_active(self) -> list[Squad]:
        stmt = select(Squad).where(Squad.is_active.is_(True)).order_by(Squad.name)
        return list(await self._session.scalars(stmt))

    async def list_by_ids(self, squad_ids: list[int]) -> list[Squad]:
        if not squad_ids:
            return []
        stmt = select(Squad).where(Squad.id.in_(squad_ids)).order_by(Squad.name)
        return list(await self._session.scalars(stmt))

    # --- группы ---
    async def get_chat_by_chat_id(self, chat_id: int) -> SquadChat | None:
        return (
            await self._session.scalars(select(SquadChat).where(SquadChat.chat_id == chat_id))
        ).first()

    async def get_chat_for_squad(self, squad_id: int) -> SquadChat | None:
        return (
            await self._session.scalars(select(SquadChat).where(SquadChat.squad_id == squad_id))
        ).first()

    async def list_chats(self) -> list[SquadChat]:
        stmt = (
            select(SquadChat)
            .join(Squad, Squad.id == SquadChat.squad_id)
            .where(Squad.is_active.is_(True))
        )
        return list(await self._session.scalars(stmt))

    async def add_chat(self, chat: SquadChat) -> SquadChat:
        self._session.add(chat)
        await self._session.flush()
        return chat

    # --- топики ---
    async def get_topic(self, squad_id: int, purpose: TopicPurpose) -> SquadTopic | None:
        stmt = select(SquadTopic).where(
            SquadTopic.squad_id == squad_id, SquadTopic.purpose == purpose
        )
        return (await self._session.scalars(stmt)).first()

    async def upsert_topic(
        self, squad_id: int, purpose: TopicPurpose, thread_id: int | None
    ) -> None:
        stmt = insert(SquadTopic).values(
            squad_id=squad_id, purpose=purpose, message_thread_id=thread_id
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[SquadTopic.squad_id, SquadTopic.purpose],
            set_={"message_thread_id": thread_id},
        )
        await self._session.execute(stmt)
