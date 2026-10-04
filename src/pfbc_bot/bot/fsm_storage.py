"""FSM-хранилище aiogram в PostgreSQL: состояние диалогов переживает рестарт, Redis не нужен."""

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder, StateType, StorageKey
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pfbc_bot.db.models import FsmState


class PostgresStorage(BaseStorage):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._key_builder = DefaultKeyBuilder(with_bot_id=True, with_destiny=True)

    def _key(self, key: StorageKey) -> str:
        return self._key_builder.build(key)

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await self._upsert(self._key(key), {"state": value})

    async def get_state(self, key: StorageKey) -> str | None:
        async with self._session_factory() as session:
            row = await session.get(FsmState, self._key(key))
            return row.state if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._upsert(self._key(key), {"data": dict(data)})

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await session.get(FsmState, self._key(key))
            return dict(row.data) if row else {}

    async def close(self) -> None:
        return None

    async def _upsert(self, key: str, values: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            stmt = insert(FsmState).values(key=key, **{"state": None, "data": {}, **values})
            stmt = stmt.on_conflict_do_update(index_elements=[FsmState.key], set_=values)
            await session.execute(stmt)
            # пустые записи (диалог завершён) не храним
            row = (await session.scalars(select(FsmState).where(FsmState.key == key))).first()
            if row is not None and row.state is None and not row.data:
                await session.execute(delete(FsmState).where(FsmState.key == key))
            await session.commit()
