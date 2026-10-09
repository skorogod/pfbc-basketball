"""Исправления по итогам ревью: жизненный цикл опросов и сборов, публикация, перенос событий."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import EventType, PollType, SquadRole
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.schemas.payments import GamePaymentCreate, PaymentDetails, PaymentMethod
from pfbc_bot.schemas.trainings import TrainingCreate
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict, PermissionDenied

from .conftest import FakeNotifier
from .helpers import CHAT_ID, GAMES_THREAD, ctx_of, join, setup_squad

MSK = "Europe/Moscow"
DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
COACH, CAPTAIN = 100, 101
DETAILS = PaymentDetails(method=PaymentMethod.PHONE, value="+79991234567", bank="Сбербанк")


def at(hour: int, day: date = DAY) -> datetime:
    return local_to_utc(day, time(hour, 0), MSK)


@pytest.fixture
async def squad_id(services: Services) -> int:
    squad = await setup_squad(services)
    await join(services, COACH, at=LONG_AGO, role=SquadRole.COACH)
    await join(services, CAPTAIN, at=LONG_AGO, role=SquadRole.CAPTAIN)
    for tg_id in (1, 2):
        await join(services, tg_id, at=LONG_AGO)
    return squad.id


async def _match(services: Services, squad_id: int, *, publish: bool = True) -> int:
    match = await services.events.create(
        await ctx_of(services, COACH),
        EventCreate(
            squad_id=squad_id,
            type=EventType.MATCH,
            title="PFBC — Динамо",
            starts_at=at(12),
            notify_group=publish,
        ),
        now=NOW,
    )
    return match.id


def _payment(match_id: int, collector_id: int, deadline: datetime | None) -> GamePaymentCreate:
    return GamePaymentCreate(
        match_event_id=match_id,
        collector_user_id=collector_id,
        amount=Decimal("1000"),
        deadline_at=deadline,
        details=DETAILS,
    )


async def test_publication_result_is_reported(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    """Опрос не принят — событие сохранено, а тренер узнаёт: пост есть, опроса нет."""
    notifier.fail_polls_on = {1}
    coach = await ctx_of(services, COACH)
    match = await services.events.create(
        coach,
        EventCreate(
            squad_id=squad_id,
            type=EventType.MATCH,
            title="Матч",
            starts_at=at(12),
            notify_group=True,
        ),
        now=NOW,
    )
    assert match.posted is True and match.poll_posted is False
    meeting = await services.events.create(
        coach,
        EventCreate(
            squad_id=squad_id,
            type=EventType.MEETING,
            title="Собрание",
            starts_at=at(19),
            notify_group=True,
        ),
        now=NOW,
    )
    assert meeting.posted is True and meeting.poll_posted is None  # у собрания опроса нет


async def test_game_payment_poll_failure_removes_post(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    match_id = await _match(services, squad_id)  # poll-1 — опрос участия
    coach = await ctx_of(services, COACH)
    notifier.fail_polls_on = {2}
    with pytest.raises(Conflict):
        await services.game_payments.create(coach, _payment(match_id, coach.user.id, None), now=NOW)
    post_id = notifier.to(CHAT_ID)[-1].message_id
    assert notifier.deleted == [(CHAT_ID, post_id)]  # пост без опроса удалён
    assert await services.game_payments.status(coach, match_id) is None  # сбора нет


async def test_one_active_payment_per_match_under_race(
    services: Services, squad_id: int, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    match_id = await _match(services, squad_id)
    coach = await ctx_of(services, COACH)
    await asyncio.gather(
        services.game_payments.create(coach, _payment(match_id, coach.user.id, None), now=NOW),
        services.game_payments.create(coach, _payment(match_id, coach.user.id, None), now=NOW),
    )
    async with uow_factory() as uow:
        assert (await uow.game_payments.active_for_event(match_id)) is not None
        rows = await uow.session.execute(
            text("SELECT count(*) FROM game_payments WHERE event_id = :e AND cancelled_at IS NULL"),
            {"e": match_id},
        )
        assert rows.scalar() == 1
    assert len(notifier.stopped_polls) == 1  # опрос «Оплатил» проигравшего — закрыт


async def test_deleting_match_cancels_payment(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    match_id = await _match(services, squad_id)
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, _payment(match_id, coach.user.id, NOW + timedelta(hours=1)), now=NOW
    )
    await services.events.delete(coach, match_id, now=NOW)
    assert sorted(notifier.stopped_polls) == [(CHAT_ID, 1001), (CHAT_ID, 1002)]
    assert await services.game_payments.remind_due(now=NOW + timedelta(hours=2)) == 0


async def test_polls_close_when_event_starts(
    services: Services, squad_id: int, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    """Опрос участия закрывается в начале события; опрос «Оплатил» — нет (можно позже)."""
    match_id = await _match(services, squad_id)
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(coach, _payment(match_id, coach.user.id, None), now=NOW)
    assert await services.polls.close_due(now=at(11)) == 0
    assert await services.polls.close_due(now=at(12)) == 1
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]
    assert await services.polls.close_due(now=at(13)) == 0
    async with uow_factory() as uow:  # ответы закрытого опроса по-прежнему учитываются
        poll = await uow.polls.get_for_ref(PollType.MATCH, "event", match_id)
        assert poll is not None and poll.closed_at is not None


async def test_cancelled_training_closes_its_poll(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    coach = await ctx_of(services, COACH)
    training = await services.trainings.add_one_off(
        coach,
        TrainingCreate(squad_id=squad_id, starts_at=at(19), venue="Зал №5"),
        now=NOW,
    )
    await services.polls.start_training_poll(coach, training.id, now=NOW)
    await services.trainings.cancel(coach, training.id, now=NOW)
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]
    assert any("отменена" in m.text for m in notifier.to(CHAT_ID))


async def test_manual_training_poll_failure_returns_claim(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    """Telegram не принял ручной опрос — планировщик опубликует его сам в обычное время."""
    coach = await ctx_of(services, COACH)
    training = await services.trainings.add_one_off(
        coach, TrainingCreate(squad_id=squad_id, starts_at=at(19), venue="Зал №5"), now=NOW
    )
    notifier.fail_polls_on = {1}
    with pytest.raises(Conflict):
        await services.polls.start_training_poll(coach, training.id, now=at(8))
    assert await services.polls.publish_training_polls(now=at(9)) == 1


async def test_move_event_republishes_poll_and_reminders(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    match_id = await _match(services, squad_id)
    coach = await ctx_of(services, COACH)
    await services.events.send_due_reminders(now=at(9))  # «сегодня» по старой дате
    reminders_before = len([m for m in notifier.to(CHAT_ID) if "Напоминание" in m.text])

    moved = await services.events.move(coach, match_id, at(12, DAY + timedelta(days=2)), now=NOW)
    assert moved.posted and moved.poll_posted
    group = notifier.to(CHAT_ID)[-1]
    assert group.thread_id == GAMES_THREAD and "Перенесено" in group.text
    assert "12.10.2026 12:00" in group.text and "14.10.2026 12:00" in group.text
    assert (CHAT_ID, 1001) in notifier.stopped_polls  # старый опрос закрыт, новый — poll-2
    assert "14.10.2026" in notifier.polls[-1][1]

    await services.events.send_due_reminders(now=at(9, DAY + timedelta(days=2)))
    after = len([m for m in notifier.to(CHAT_ID) if "Напоминание" in m.text])
    assert after > reminders_before  # напоминание о новой дате ушло заново

    with pytest.raises(PermissionDenied):  # капитан игры не переносит
        await services.events.move(await ctx_of(services, CAPTAIN), match_id, at(20), now=NOW)
    with pytest.raises(Conflict):
        await services.events.move(coach, match_id, NOW - timedelta(hours=1), now=NOW)
