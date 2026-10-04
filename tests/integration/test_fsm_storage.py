from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pfbc_bot.bot.fsm_storage import PostgresStorage
from pfbc_bot.db.models import FsmState
from pfbc_bot.repositories.uow import UowFactory


class Form(StatesGroup):
    name = State()


async def test_postgres_fsm_storage_roundtrip(engine: AsyncEngine, uow_factory: UowFactory) -> None:
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    storage = PostgresStorage(session_factory)
    key = StorageKey(bot_id=1, chat_id=10, user_id=10)

    assert await storage.get_state(key) is None
    await storage.set_state(key, Form.name)
    await storage.update_data(key, {"squad_id": 3})
    assert await storage.get_state(key) == Form.name.state
    assert await storage.get_data(key) == {"squad_id": 3}

    # завершённый диалог не оставляет записей
    await storage.set_state(key, None)
    await storage.set_data(key, {})
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(FsmState)) == 0
