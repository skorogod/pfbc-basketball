"""Процесс worker: планировщик опросов, дежурств, поздравлений и напоминаний."""

import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from pfbc_bot.config import get_settings
from pfbc_bot.logging import get_logger, setup_logging
from pfbc_bot.runtime import build_runtime
from pfbc_bot.scheduler.jobs import build_jobs, register_jobs
from pfbc_bot.scheduler.locks import JobLocks

log = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    runtime = await build_runtime(settings)
    scheduler = AsyncIOScheduler(timezone="UTC")
    locks = JobLocks(runtime.engine)  # второй worker на той же БД задачи не задвоит
    on_start = register_jobs(
        scheduler, build_jobs(runtime.services, settings.default_timezone), locks
    )
    scheduler.start()
    log.info("worker_started", jobs=[job.id for job in scheduler.get_jobs()])
    for runner in on_start:
        await runner()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        await locks.close()
        await runtime.close()
        log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
