"""Advisory lock на задачу worker-а: второй worker на той же БД задачу не выполняет."""

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncEngine

from pfbc_bot.scheduler.jobs import Job, _safe
from pfbc_bot.scheduler.locks import JobLocks, lock_key


def test_lock_key_is_stable_and_distinct() -> None:
    assert lock_key("roster_notices") == lock_key("roster_notices")
    assert lock_key("roster_notices") != lock_key("match_sync")


async def test_second_worker_skips_locked_job(engine: AsyncEngine) -> None:
    first, second = JobLocks(engine), JobLocks(engine)  # два процесса worker
    try:
        assert await first.try_acquire("match_sync") is True
        assert await second.try_acquire("match_sync") is False  # занято первым
        assert await second.try_acquire("roster_notices") is True  # другая задача — свободна
        await second.release("roster_notices")

        runs: list[str] = []

        async def run() -> None:
            runs.append("run")

        job = Job("match_sync", run, IntervalTrigger(minutes=30))
        await _safe(job, second)()
        assert runs == []  # выполняет первый worker — второй пропускает тик

        await first.release("match_sync")
        await _safe(job, second)()
        assert runs == ["run"]
        # после выполнения блокировка снята — задачу снова может взять любой worker
        assert await first.try_acquire("match_sync") is True
        await first.release("match_sync")
    finally:
        await first.close()
        await second.close()


async def test_failed_job_releases_lock(engine: AsyncEngine) -> None:
    locks, other = JobLocks(engine), JobLocks(engine)
    try:

        async def boom() -> None:
            raise RuntimeError("job crashed")

        await _safe(Job("receipt_polls", boom, IntervalTrigger(minutes=5)), locks)()
        assert await other.try_acquire("receipt_polls") is True  # упавшая задача не держит
        await other.release("receipt_polls")
    finally:
        await locks.close()
        await other.close()


async def test_closed_connection_releases_locks(engine: AsyncEngine) -> None:
    """Worker остановился (соединение закрыто) — PostgreSQL снимает его блокировки."""
    stopped, alive = JobLocks(engine), JobLocks(engine)
    try:
        assert await stopped.try_acquire("birthdays") is True
        await stopped.close()
        assert await alive.try_acquire("birthdays") is True
        await alive.release("birthdays")
    finally:
        await alive.close()
