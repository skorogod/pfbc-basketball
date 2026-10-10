"""Квитанции за тренировки: ежемесячный опрос «Отправил», ручной запуск, напоминания после срока."""

import asyncio
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import text

from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import SquadRole, TopicPurpose
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict, PermissionDenied

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, ctx_of, join, setup_squad

MSK = "Europe/Moscow"
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
COACH, CAPTAIN = 100, 101
SENT, LATER = 0, 1
PAYMENTS_THREAD = 15


def at(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    return local_to_utc(date(2026, month, day), time(hour, minute), MSK)


@pytest.fixture
async def squad_id(services: Services) -> int:
    """Тренер, капитан и игроки 1–3; опрос по умолчанию — 1-го числа в 10:00, срок — сутки."""
    squad = await setup_squad(services)
    await join(services, COACH, at=LONG_AGO, role=SquadRole.COACH)
    await join(services, CAPTAIN, at=LONG_AGO, role=SquadRole.CAPTAIN)
    for tg_id in (1, 2, 3):
        await join(services, tg_id, at=LONG_AGO)
    return squad.id


async def test_monthly_poll_and_reminders(
    services: Services, notifier: FakeNotifier, squad_id: int
) -> None:
    assert await services.receipts.publish_due(now=at(1, 9, 59)) == 0
    assert await services.receipts.publish_due(now=at(1, 10)) == 1
    assert await services.receipts.publish_due(now=at(1, 10, 5)) == 0  # один раз в месяц

    post = notifier.to(CHAT_ID)[-1]
    assert post.thread_id is None  # топик «Оплата» не назначен, общий тоже — основной раздел
    assert "квитанц" in post.text.lower() and "ноябрь 2026" in post.text
    assert '<a href="tg://user?id=100">user100</a>' in post.text  # кому присылать
    assert "02.11.2026 10:00" in post.text  # срок — сутки
    assert notifier.polls[-1] == (CHAT_ID, "🧾 Квитанция за ноябрь 2026: отправили тренеру?", None)

    await services.polls.record_answer("poll-1", 1, [SENT], now=at(1, 11))
    await services.polls.record_answer("poll-1", 2, [LATER], now=at(1, 11))
    await services.polls.record_answer("poll-1", COACH, [SENT], now=at(1, 11))

    assert await services.receipts.remind_due(now=at(2, 9)) == 0
    assert await services.receipts.remind_due(now=at(2, 10)) == 1
    assert await services.receipts.remind_due(now=at(2, 10, 5)) == 0

    # напоминание — участникам без «Отправил», в том числе капитану; тренеру — нет
    for tg_id in (2, 3, CAPTAIN):
        assert "Напоминание: квитанция за ноябрь 2026" in notifier.to(tg_id)[-1].text
    assert not notifier.to(1)
    assert not notifier.to(COACH)  # итог тренеру не шлётся — он виден в опросе


async def test_repeated_reminders(
    services: Services, notifier: FakeNotifier, squad_id: int
) -> None:
    """2 напоминания раз в сутки; срок в тихие часы — первое в 08:00."""
    admin = await ctx_of(services, ADMIN_TG_ID)
    await services.settings.set_squad_value(admin, squad_id, "receipt_reminder_count", "2")
    coach = await ctx_of(services, COACH)
    await services.receipts.create(coach, squad_id, at(3, 1), now=at(1, 12))

    assert await services.receipts.remind_due(now=at(3, 1)) == 0  # 01:00 — тихие часы
    assert await services.receipts.remind_due(now=at(3, 8)) == 1
    await services.polls.record_answer("poll-1", 1, [SENT], now=at(3, 9))
    assert await services.receipts.remind_due(now=at(4, 0, 30)) == 0
    assert await services.receipts.remind_due(now=at(4, 8)) == 1
    assert await services.receipts.remind_due(now=at(5, 8)) == 0  # больше двух — нет
    assert len(notifier.to(1)) == 1 and len(notifier.to(2)) == 2


async def test_payments_topic_and_group_reminder(
    services: Services, notifier: FakeNotifier, squad_id: int
) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    await services.squads.bind_topic(admin, CHAT_ID, TopicPurpose.PAYMENTS, PAYMENTS_THREAD)
    await services.receipts.publish_due(now=at(1, 10))
    assert notifier.to(CHAT_ID)[-1].thread_id == PAYMENTS_THREAD

    notifier.blocked_dm = {2}
    await services.receipts.remind_due(now=at(2, 10))
    group = notifier.to(CHAT_ID)[-1]
    assert group.thread_id == PAYMENTS_THREAD
    assert '<a href="tg://user?id=2">user2</a>' in group.text and "user1" not in group.text


async def test_manual_poll_blocks_scheduled_one(
    services: Services, notifier: FakeNotifier, squad_id: int
) -> None:
    coach = await ctx_of(services, COACH)
    assert await services.receipts.current(coach, squad_id, now=at(1, 8)) is None
    month = await services.receipts.create(coach, squad_id, at(5, 20), now=at(1, 8))
    assert month == "ноябрь 2026"
    assert "05.11.2026 20:00" in notifier.to(CHAT_ID)[-1].text  # свой срок

    # в день расписания бот опрос не публикует — он уже есть в этом месяце
    assert await services.receipts.publish_due(now=at(1, 10)) == 0
    assert len(notifier.polls) == 1
    # повторный ручной запуск — отказ с тем, кто уже отметился
    await services.polls.record_answer("poll-1", 1, [SENT], now=at(1, 9))
    already = await services.receipts.current(coach, squad_id, now=at(2, 8))
    assert already is not None and "Отметили «Отправил»: 1 из 4" in already.text
    with pytest.raises(Conflict, match="уже опубликован"):
        await services.receipts.create(coach, squad_id, at(6, 20), now=at(2, 8))

    # напоминание — по своему сроку
    assert await services.receipts.remind_due(now=at(2, 10)) == 0
    assert await services.receipts.remind_due(now=at(5, 20)) == 1
    # в следующем месяце — снова по расписанию
    assert await services.receipts.publish_due(now=at(1, 10, month=12)) == 1


async def test_rules(services: Services, notifier: FakeNotifier, squad_id: int) -> None:
    with pytest.raises(PermissionDenied):  # по умолчанию — тренер (и админ)
        await services.receipts.create(
            await ctx_of(services, CAPTAIN), squad_id, at(5, 20), now=at(1, 8)
        )
    coach = await ctx_of(services, COACH)
    with pytest.raises(Conflict):
        await services.receipts.create(coach, squad_id, at(1, 7), now=at(1, 8))
    # бот был выключен в день опроса: в течение суток публикует, позже — нет
    assert await services.receipts.publish_due(now=at(2, 10)) == 0

    admin = await ctx_of(services, ADMIN_TG_ID)
    await services.settings.set_squad_value(admin, squad_id, "receipt_poll_day", "15")
    await services.settings.set_squad_value(admin, squad_id, "receipt_deadline_hours", "48")
    assert await services.receipts.publish_due(now=at(15, 9)) == 0
    assert await services.receipts.publish_due(now=at(15, 10)) == 1
    assert "17.11.2026 10:00" in notifier.to(CHAT_ID)[-1].text

    await services.settings.set_squad_value(admin, squad_id, "features.receipt_reminders", "false")
    assert await services.receipts.publish_due(now=at(15, 10, month=12)) == 0


async def test_concurrent_manual_and_scheduled_publish_once(
    services: Services, notifier: FakeNotifier, squad_id: int, uow_factory: UowFactory
) -> None:
    coach = await ctx_of(services, COACH)
    results = await asyncio.gather(
        services.receipts.create(coach, squad_id, at(5, 20), now=at(1, 10)),
        services.receipts.create(coach, squad_id, at(5, 20), now=at(1, 10)),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["Conflict", "str"]
    async with uow_factory() as uow:
        rows = await uow.session.execute(text("SELECT count(*) FROM receipt_requests"))
        assert rows.scalar() == 1
        polls = await uow.session.execute(text("SELECT count(*) FROM polls WHERE type='receipt'"))
        assert polls.scalar() == 1
    # пост и опрос проигравшего удалены из группы
    assert len(notifier.deleted) in (0, 2)
    if notifier.deleted:
        assert notifier.deleted[0][0] == CHAT_ID


async def test_closed_receipt_poll_stops_reminders(
    services: Services, notifier: FakeNotifier, squad_id: int
) -> None:
    coach = await ctx_of(services, COACH)
    await services.receipts.create(coach, squad_id, at(2, 10), now=at(1, 8))
    current = await services.receipts.current(coach, squad_id, now=at(1, 9))
    assert current is not None and not current.closed
    assert await services.receipts.close(coach, current.request_id, now=at(1, 9)) == "ноябрь 2026"
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]
    assert await services.receipts.close(coach, current.request_id, now=at(1, 9)) is None
    current = await services.receipts.current(coach, squad_id, now=at(1, 9))
    assert current is not None and current.closed
    assert await services.receipts.remind_due(now=at(2, 10)) == 0
    # завершённый сбор месяц не освобождает — по расписанию опрос не публикуется
    assert await services.receipts.publish_due(now=at(1, 10)) == 0
