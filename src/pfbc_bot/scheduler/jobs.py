"""Плановые задачи worker-а.

Каждая задача — «тик»: сама смотрит в БД, что пора сделать. Идемпотентность — reminder_log,
поэтому рестарт безопасен, а пропущенное догоняется следующим тиком.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from pfbc_bot.logging import get_logger
from pfbc_bot.services.container import Services

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    run: Callable[[], Awaitable[Any]]
    trigger: IntervalTrigger | CronTrigger
    run_on_start: bool = False


def build_jobs(services: Services, timezone: str) -> list[Job]:
    every = IntervalTrigger
    return [
        Job(
            "materialize_trainings",
            services.trainings.materialize,
            every(hours=1),
            run_on_start=True,
        ),
        Job("training_polls", services.polls.publish_training_polls, every(minutes=1)),
        Job("close_polls", services.polls.close_due, every(minutes=5)),
        Job("assign_duty", services.duty.assign_due, every(minutes=1)),
        Job("complete_duty", services.duty.complete_due, every(minutes=5)),
        Job("birthdays", services.birthdays.send_due, every(minutes=5)),
        Job("birthday_staff_reminders", services.birthdays.send_staff_reminders, every(minutes=5)),
        Job("event_reminders", services.events.send_due_reminders, every(minutes=5)),
        Job("review_offers", services.events.offer_reviews, every(minutes=5)),
        # тик; в mtgame — только пока ищется следующая игра (со вторника) или с мониторингом
        Job("match_sync", services.matches.sync, every(minutes=30), run_on_start=True),
        Job("game_payment_reminders", services.game_payments.remind_due, every(minutes=5)),
        Job("receipt_polls", services.receipts.publish_due, every(minutes=5)),
        Job("receipt_reminders", services.receipts.remind_due, every(minutes=5)),
        Job(
            "reconcile_memberships",
            services.membership.reconcile,
            CronTrigger(hour=4, minute=0, timezone=timezone),
        ),
        Job(
            "anonymize_inactive",
            services.membership.anonymize_expired,
            CronTrigger(hour=4, minute=30, timezone=timezone),
        ),
    ]


def _safe(job: Job) -> Callable[[], Awaitable[None]]:
    async def runner() -> None:
        try:
            result = await job.run()
            log.debug("job_done", job=job.name, result=result)
        except Exception:
            log.exception("job_failed", job=job.name)

    return runner


def register_jobs(
    scheduler: AsyncIOScheduler, jobs: list[Job]
) -> list[Callable[[], Awaitable[None]]]:
    """Регистрирует задачи; возвращает те, что нужно выполнить сразу при старте."""
    on_start = []
    for job in jobs:
        runner = _safe(job)
        scheduler.add_job(
            runner,
            job.trigger,
            id=job.name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        if job.run_on_start:
            on_start.append(runner)
    return on_start
