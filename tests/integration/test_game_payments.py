"""Сбор денег за игру: пост и опрос «Оплатил», напоминания игрокам после дедлайна."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import EventType, SquadRole
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.schemas.payments import GamePaymentCreate, PaymentDetails, PaymentMethod
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict, PermissionDenied

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, GAMES_THREAD, ctx_of, join, setup_squad

MSK = "Europe/Moscow"
DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
COACH, CAPTAIN = 100, 101
COACH_OPT, PLAYER_OPT, FAN_OPT, NO_OPT = 0, 1, 2, 3
PAID, LATER = 0, 1
DETAILS = PaymentDetails(method=PaymentMethod.PHONE, value="+79991234567", bank="Сбербанк")


def at(hour: int, day: date = DAY) -> datetime:
    return local_to_utc(day, time(hour, 0), MSK)


@pytest.fixture
async def match_id(services: Services) -> int:
    """Матч 12.10 12:00 с опросом участия (poll-1): 1, 2 — игроки, 3 — болельщик, 4 — молчит."""
    squad = await setup_squad(services)
    await join(services, COACH, at=LONG_AGO, role=SquadRole.COACH)
    await join(services, CAPTAIN, at=LONG_AGO, role=SquadRole.CAPTAIN)
    for tg_id in (1, 2, 3, 4):
        await join(services, tg_id, at=LONG_AGO)
    match = await services.events.create(
        await ctx_of(services, COACH),
        EventCreate(
            squad_id=squad.id,
            type=EventType.MATCH,
            title="PFBC — Динамо",
            starts_at=at(12),
            place="ФОК",
            notify_group=True,
        ),
        now=NOW,
    )
    for tg_id, option in ((1, PLAYER_OPT), (2, PLAYER_OPT), (3, FAN_OPT), (COACH, COACH_OPT)):
        await services.polls.record_answer("poll-1", tg_id, [option], now=NOW)
    return match.id


def payment(match_id: int, deadline: datetime | None, collector_id: int) -> GamePaymentCreate:
    return GamePaymentCreate(
        match_event_id=match_id,
        collector_user_id=collector_id,
        amount=Decimal("2500"),
        deadline_at=deadline,
        details=DETAILS,
    )


async def test_payment_post_poll_and_reminders(
    services: Services, notifier: FakeNotifier, match_id: int
) -> None:
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, payment(match_id, NOW + timedelta(days=1), coach.user.id), now=NOW
    )

    post = notifier.to(CHAT_ID)[-1]
    assert post.thread_id == GAMES_THREAD
    assert "PFBC — Динамо" in post.text and "ФОК" in post.text
    assert "Сумма: 2 500 ₽" in post.text and "+7 999 123-45-67" in post.text
    assert "Оплатить до" in post.text
    assert notifier.polls[-1] == (CHAT_ID, "💰 Оплата игры «PFBC — Динамо»: 2 500 ₽", GAMES_THREAD)
    assert await services.game_payments.saved_details(coach.user.id) == DETAILS

    await services.polls.record_answer("poll-2", 1, [PAID], now=NOW)  # игрок оплатил
    await services.polls.record_answer("poll-2", 3, [PAID], now=NOW)  # болельщик оплатил
    await services.polls.record_answer("poll-2", 2, [LATER], now=NOW)  # «оплачу позже»

    status = await services.game_payments.status(coach, match_id)
    assert status is not None
    assert [s.count("user1") for s in status.paid] == [1]
    assert [s.count("user2") for s in status.unpaid] == [1]
    assert [s.count("user3") for s in status.others_paid] == [1]

    assert await services.game_payments.remind_due(now=NOW + timedelta(hours=23)) == 0
    assert await services.game_payments.remind_due(now=NOW + timedelta(days=1)) == 1
    assert await services.game_payments.remind_due(now=NOW + timedelta(days=1, minutes=5)) == 0

    # напоминание — только игроку без «Оплатил»; болельщику и оплатившим — нет
    assert "Напоминание об оплате" in notifier.to(2)[-1].text
    assert "+7 999 123-45-67" in notifier.to(2)[-1].text
    assert not notifier.to(1) and not notifier.to(3) and not notifier.to(4)
    summary = notifier.to(COACH)[-1].text
    assert "Оплатили: 1 из 2" in summary and "user2" in summary


async def test_reminder_in_group_when_dm_closed(
    services: Services, notifier: FakeNotifier, match_id: int
) -> None:
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, payment(match_id, NOW + timedelta(hours=2), coach.user.id), now=NOW
    )
    notifier.blocked_dm = {2}
    assert await services.game_payments.remind_due(now=NOW + timedelta(hours=3)) == 1
    group = notifier.to(CHAT_ID)[-1]
    assert group.thread_id == GAMES_THREAD
    # в группе — только тот, кому не дошло личное сообщение
    assert '<a href="tg://user?id=2">user2</a>' in group.text and "user1" not in group.text
    assert "Напоминание об оплате" in notifier.to(1)[-1].text


async def test_new_payment_replaces_previous_and_rules(
    services: Services, notifier: FakeNotifier, match_id: int
) -> None:
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, payment(match_id, NOW + timedelta(hours=2), coach.user.id), now=NOW
    )
    await services.game_payments.create(coach, payment(match_id, None, coach.user.id), now=NOW)
    assert notifier.stopped_polls == [(CHAT_ID, 1002)]  # прежний опрос «Оплатил» закрыт
    # у действующего сбора срока нет — напоминаний нет, у прежнего — отменены
    assert await services.game_payments.remind_due(now=NOW + timedelta(hours=3)) == 0

    with pytest.raises(PermissionDenied):  # собирает деньги тренер
        await services.game_payments.create(
            await ctx_of(services, CAPTAIN), payment(match_id, None, coach.user.id), now=NOW
        )
    with pytest.raises(Conflict):
        await services.game_payments.create(coach, payment(match_id, NOW, coach.user.id), now=NOW)


async def test_payment_status_in_summary_absent_without_payment(
    services: Services, match_id: int
) -> None:
    coach = await ctx_of(services, COACH)
    assert await services.game_payments.status(coach, match_id) is None
    assert [m.id for m in await services.game_payments.matches(coach, 1, now=NOW)] == [match_id]


async def test_admin_collects_for_coach(
    services: Services, notifier: FakeNotifier, match_id: int
) -> None:
    """Сбор создаёт админ, деньги получает тренер. Получателем можно выбрать только тренера
    или админа, который сам сохранил реквизиты; чужие реквизиты задать или подменить нельзя."""
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach = await ctx_of(services, COACH)
    squad_id = (await services.events.get(match_id)).squad_id

    # пока тренер не указал реквизиты сам — выбрать его получателем нельзя
    assert [u.id for u in await services.game_payments.collectors(admin, squad_id)] == [
        admin.user.id
    ]
    with pytest.raises(Conflict):
        await services.game_payments.create(admin, payment(match_id, None, coach.user.id), now=NOW)

    await services.game_payments.save_details(coach, DETAILS)  # тренер — сам, в /profile
    collectors = [u.id for u in await services.game_payments.collectors(admin, squad_id)]
    assert collectors == [admin.user.id, coach.user.id]  # сам создающий — первым

    forged = PaymentDetails(method=PaymentMethod.CARD, value="4111111111111111", bank="Чужой")
    await services.game_payments.create(
        admin,
        payment(match_id, NOW + timedelta(hours=2), coach.user.id).model_copy(
            update={"details": forged}
        ),
        now=NOW,
    )
    post = notifier.to(CHAT_ID)[-1].text
    assert '👤 Получатель: <a href="tg://user?id=100">user100</a>' in post
    assert "+7 999 123-45-67" in post and "4111" not in post  # реквизиты — тренера
    assert await services.game_payments.saved_details(coach.user.id) == DETAILS
    assert await services.game_payments.saved_details(admin.user.id) is None

    await services.game_payments.remind_due(now=NOW + timedelta(hours=3))
    assert "Срок оплаты прошёл" in notifier.to(COACH)[-1].text
    assert "Срок оплаты прошёл" in notifier.to(ADMIN_TG_ID)[-1].text

    with pytest.raises(Conflict):  # игрок без роли тренера/админа — не получатель
        await services.game_payments.collector(admin, squad_id, 10_000)
