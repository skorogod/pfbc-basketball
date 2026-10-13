"""Advisory lock PostgreSQL на задачу worker-а: одну задачу одновременно выполняет только один
worker на БД (второй — масштабирование или ошибочный запуск — пропускает тик).

Блокировки — session-level, на одном выделенном соединении (autocommit): соединение из пула
занято одно, а не по одному на задачу. Запросы на нём идут по очереди (asyncio.Lock) — это
быстрые вызовы, сами задачи выполняются параллельно в своих сессиях. Соединение оборвалось или
закрыто (оно не возвращается в пул, а закрывается физически) — PostgreSQL сам снимает его
блокировки, при следующем захвате оно открывается заново.
"""

import asyncio
import hashlib

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from pfbc_bot.logging import get_logger

log = get_logger(__name__)
NAMESPACE = "pfbc_job:"


def lock_key(name: str) -> int:
    """Стабильный bigint-ключ блокировки по имени задачи (одинаковый во всех процессах)."""
    digest = hashlib.sha256((NAMESPACE + name).encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class JobLocks:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._conn: AsyncConnection | None = None
        self._mutex = asyncio.Lock()

    async def try_acquire(self, name: str) -> bool:
        """True — блокировка наша, задачу можно выполнять; False — её держит другой worker
        (или БД недоступна: тогда и задача не выполнится)."""
        async with self._mutex:
            try:
                conn = await self._connection()
                got = await conn.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key(name)}
                )
                return bool(got)
            except (DBAPIError, InterfaceError, OperationalError, OSError):
                log.warning("job_lock_failed", job=name, exc_info=True)
                await self._drop()
                return False

    async def release(self, name: str) -> None:
        async with self._mutex:
            if self._conn is None:
                return  # соединение пропало — PostgreSQL блокировку уже снял
            try:
                await self._conn.scalar(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key(name)}
                )
            except (DBAPIError, InterfaceError, OperationalError, OSError):
                log.warning("job_unlock_failed", job=name, exc_info=True)
                await self._drop()

    async def close(self) -> None:
        async with self._mutex:
            await self._drop()

    async def _connection(self) -> AsyncConnection:
        if self._conn is None or self._conn.closed or self._conn.invalidated:
            conn = await self._engine.connect()
            self._conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        return self._conn

    async def _drop(self) -> None:
        """Физически закрыть соединение. Простой close() вернул бы его в пул вместе с
        session-level блокировками — они бы «зависли» и достались чужому запросу из пула."""
        if self._conn is not None:
            try:
                await self._conn.invalidate()
                await self._conn.close()
            except Exception:  # соединение уже мёртвое — закрывать нечего
                log.debug("job_lock_connection_close_failed", exc_info=True)
            self._conn = None
