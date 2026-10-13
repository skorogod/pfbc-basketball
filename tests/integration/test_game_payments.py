"""Сбор денег за игру: пост и опрос «Оплатил», напоминания игрокам после дедлайна."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import EventType, SquadRole
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.schemas.payments import GamePaymentCreate, PaymentDetails, PaymentMethod
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict, NotFound, PermissionDenied

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, MATCH_THREAD, ctx_of, join, setup_squad

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


@pytest.fixture
async def roster(services: Services, match_id: int) -> list[int]:
    """Заявка — игроки 1 и 2 (они же ответили «Буду (игрок)»)."""
    return [(await ctx_of(services, tg_id)).user.id for tg_id in (1, 2)]


def payment(
    match_id: int, deadline: datetime | None, collector_id: int, roster: list[int]
) -> GamePaymentCreate:
    return GamePaymentCreate(
        match_event_id=match_id,
        collector_user_id=collector_id,
        amount=Decimal("2500"),
        deadline_at=deadline,
        details=DETAILS,
        player_ids=roster,
    )


async def test_payment_post_poll_and_reminders(
    services: Services, notifier: FakeNotifier, match_id: int, roster: list[int]
) -> None:
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, payment(match_id, NOW + timedelta(days=1), coach.user.id, roster), now=NOW
    )

    post = notifier.to(CHAT_ID)[-1]
    assert post.thread_id == MATCH_THREAD
    assert "PFBC — Динамо" in post.text and "ФОК" in post.text
    assert "Сумма: 2 500 ₽" in post.text and "+79991234567" in post.text
    assert "Оплатить до" in post.text
    assert notifier.polls[-1] == (CHAT_ID, "💰 Оплата игры «PFBC — Динамо»: 2 500 ₽", MATCH_THREAD)
    # реквизиты из сбора сами не запоминаются — тренер сохраняет их по вопросу после ввода
    assert await services.game_payments.details_of(coach.user.id) == []
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]  # голосование участия закрыто
    # закрытый опрос участия планировщик не трогает повторно
    assert await services.polls.close_due(now=at(12)) == 0

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
    assert "+79991234567" in notifier.to(2)[-1].text
    assert not notifier.to(1) and not notifier.to(3) and not notifier.to(4)
    # итог тренеру не шлётся — он виден в опросе
    assert not any("Напоминание" in m.text or "Оплатили" in m.text for m in notifier.to(COACH))


async def test_repeated_reminders_skip_quiet_hours(
    services: Services, notifier: FakeNotifier, match_id: int, roster: list[int]
) -> None:
    """3 напоминания раз в 6 часов: срок 12.10 20:00 → 20:00, 02:00 → 08:00 (тихие часы),
    08:00; оплативший после первого больше не получает."""
    admin = await ctx_of(services, ADMIN_TG_ID)
    squad_id = (await services.events.get(match_id)).squad_id
    await services.settings.set_squad_value(admin, squad_id, "game_payment_reminder_count", "3")
    await services.settings.set_squad_value(
        admin, squad_id, "game_payment_reminder_interval_hours", "6"
    )
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, payment(match_id, at(20), coach.user.id, roster), now=NOW
    )

    assert await services.game_payments.remind_due(now=at(20)) == 1
    assert len(notifier.to(1)) == len(notifier.to(2)) == 1
    await services.polls.record_answer("poll-2", 1, [PAID], now=at(21))

    next_day = DAY + timedelta(days=1)
    assert await services.game_payments.remind_due(now=at(2, next_day)) == 0  # тихие часы
    assert await services.game_payments.remind_due(now=at(7, next_day)) == 0
    # второе (02:00) и третье (08:00) совпали после сдвига — уходит одно, последнее
    assert await services.game_payments.remind_due(now=at(8, next_day)) == 1
    assert await services.game_payments.remind_due(now=at(9, next_day)) == 0
    assert len(notifier.to(1)) == 1 and len(notifier.to(2)) == 2


async def test_no_reminders_when_count_is_zero(
    services: Services, notifier: FakeNotifier, match_id: int, roster: list[int]
) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    squad_id = (await services.events.get(match_id)).squad_id
    await services.settings.set_squad_value(admin, squad_id, "game_payment_reminder_count", "0")
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, payment(match_id, at(20), coach.user.id, roster), now=NOW
    )
    assert await services.game_payments.remind_due(now=at(20)) == 0


async def test_reminder_in_group_when_dm_closed(
    services: Services, notifier: FakeNotifier, match_id: int, roster: list[int]
) -> None:
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, payment(match_id, NOW + timedelta(hours=2), coach.user.id, roster), now=NOW
    )
    notifier.blocked_dm = {2}
    assert await services.game_payments.remind_due(now=NOW + timedelta(hours=3)) == 1
    group = notifier.to(CHAT_ID)[-1]
    assert group.thread_id == MATCH_THREAD
    # в группе — только тот, кому не дошло личное сообщение
    assert '<a href="tg://user?id=2">user2</a>' in group.text and "user1" not in group.text
    assert "Напоминание об оплате" in notifier.to(1)[-1].text


async def test_new_payment_replaces_previous_and_rules(
    services: Services, notifier: FakeNotifier, match_id: int, roster: list[int]
) -> None:
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, payment(match_id, NOW + timedelta(hours=2), coach.user.id, roster), now=NOW
    )
    await services.game_payments.create(
        coach, payment(match_id, None, coach.user.id, roster), now=NOW
    )
    # первый сбор закрыл опрос участия, второй — прежний опрос «Оплатил»
    assert notifier.stopped_polls == [(CHAT_ID, 1001), (CHAT_ID, 1002)]
    # у действующего сбора срока нет — напоминаний нет, у прежнего — отменены
    assert await services.game_payments.remind_due(now=NOW + timedelta(hours=3)) == 0

    with pytest.raises(PermissionDenied):  # собирает деньги тренер
        await services.game_payments.create(
            await ctx_of(services, CAPTAIN), payment(match_id, None, coach.user.id, roster), now=NOW
        )
    with pytest.raises(Conflict):
        await services.game_payments.create(
            coach, payment(match_id, NOW, coach.user.id, roster), now=NOW
        )


async def test_payment_status_in_summary_absent_without_payment(
    services: Services, match_id: int
) -> None:
    coach = await ctx_of(services, COACH)
    assert await services.game_payments.status(coach, match_id) is None
    assert [m.id for m in await services.game_payments.matches(coach, 1, now=NOW)] == [match_id]


async def test_admin_collects_for_coach(
    services: Services, notifier: FakeNotifier, match_id: int, roster: list[int]
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
        await services.game_payments.create(
            admin, payment(match_id, None, coach.user.id, roster), now=NOW
        )

    await services.game_payments.add_details(coach, DETAILS)  # тренер — сам, в /profile
    collectors = [u.id for u in await services.game_payments.collectors(admin, squad_id)]
    assert collectors == [admin.user.id, coach.user.id]  # сам создающий — первым

    # подменить реквизиты чужого получателя нельзя — только одни из его сохранённых
    forged = PaymentDetails(method=PaymentMethod.CARD, value="4111111111111111", bank="Чужой")
    with pytest.raises(Conflict):
        await services.game_payments.create(
            admin,
            payment(match_id, NOW + timedelta(hours=2), coach.user.id, roster).model_copy(
                update={"details": forged}
            ),
            now=NOW,
        )
    await services.game_payments.create(
        admin, payment(match_id, NOW + timedelta(hours=2), coach.user.id, roster), now=NOW
    )
    post = notifier.to(CHAT_ID)[-1].text
    assert '👤 Получатель: <a href="tg://user?id=100">user100</a>' in post
    assert "+79991234567" in post and "4111" not in post  # реквизиты — тренера
    assert [d.same_as(DETAILS) for d in await services.game_payments.details_of(coach.user.id)] == [
        True
    ]
    assert await services.game_payments.details_of(admin.user.id) == []

    # после срока итог получателю и создателю не шлётся — он виден в опросе
    await services.game_payments.remind_due(now=NOW + timedelta(hours=3))
    assert not any("Срок оплаты прошёл" in m.text for m in notifier.to(COACH))
    assert not any("Срок оплаты прошёл" in m.text for m in notifier.to(ADMIN_TG_ID))

    with pytest.raises(Conflict):  # игрок без роли тренера/админа — не получатель
        await services.game_payments.collector(admin, squad_id, 10_000)


async def test_default_deadline_is_configurable(services: Services, match_id: int) -> None:
    coach = await ctx_of(services, COACH)
    squad_id = (await services.events.get(match_id)).squad_id
    assert await services.game_payments.default_deadline(coach, squad_id, now=NOW) == (
        NOW + timedelta(days=1)
    )
    admin = await ctx_of(services, ADMIN_TG_ID)
    await services.settings.set_squad_value(admin, squad_id, "game_payment_deadline_hours", "48")
    assert await services.game_payments.default_deadline(coach, squad_id, now=NOW) == (
        NOW + timedelta(hours=48)
    )
    with pytest.raises(PermissionDenied):  # срок по умолчанию — тому, кто собирает деньги
        await services.game_payments.default_deadline(
            await ctx_of(services, CAPTAIN), squad_id, now=NOW
        )


async def test_closed_payment_poll_stops_reminders(
    services: Services, notifier: FakeNotifier, match_id: int, roster: list[int]
) -> None:
    """Сбор завершён кнопкой (бот закрывает опрос) или опрос закрыт в Telegram — не напоминаем."""
    coach = await ctx_of(services, COACH)
    created = await services.game_payments.create(
        coach, payment(match_id, NOW + timedelta(hours=2), coach.user.id, roster), now=NOW
    )
    with pytest.raises(PermissionDenied):
        await services.game_payments.close(await ctx_of(services, CAPTAIN), created.id, now=NOW)
    assert await services.game_payments.close(coach, created.id, now=NOW) is True
    assert notifier.stopped_polls[-1] == (CHAT_ID, 1002)
    assert await services.game_payments.close(coach, created.id, now=NOW) is False
    status = await services.game_payments.status(coach, match_id)
    assert status is not None and status.closed
    assert await services.game_payments.remind_due(now=NOW + timedelta(hours=3)) == 0

    # новый сбор; его опрос закрыли в Telegram — бот узнаёт из обновления опроса
    await services.game_payments.create(
        coach, payment(match_id, at(20), coach.user.id, roster), now=NOW
    )
    assert await services.polls.mark_closed("poll-3", now=NOW) is True
    assert await services.polls.mark_closed("poll-3", now=NOW) is False
    assert await services.game_payments.remind_due(now=at(20)) == 0


async def test_several_details_managed_by_owner(services: Services, match_id: int) -> None:
    """Несколько реквизитов: добавить, изменить, удалить — только свои; лимит."""
    coach = await ctx_of(services, COACH)
    card = PaymentDetails(method=PaymentMethod.CARD, value="4111111111111111", bank="Т-Банк")
    first = await services.game_payments.add_details(coach, DETAILS)
    second = await services.game_payments.add_details(coach, card)
    assert await services.game_payments.add_details(coach, DETAILS) == first  # без дублей
    assert [d.id for d in await services.game_payments.details_of(coach.user.id)] == [
        first.id,
        second.id,
    ]
    renamed = DETAILS.model_copy(update={"bank": "Сбер"})
    updated = await services.game_payments.update_details(coach, first.id, renamed)
    assert updated.id == first.id and updated.bank == "Сбер"
    await services.game_payments.delete_details(coach, second.id)
    assert [d.bank for d in await services.game_payments.details_of(coach.user.id)] == ["Сбер"]
    with pytest.raises(NotFound):  # чужие реквизиты не изменить: id ищется только у себя
        await services.game_payments.delete_details(await ctx_of(services, CAPTAIN), first.id)
    for n in range(4):
        await services.game_payments.add_details(
            coach, PaymentDetails(method=PaymentMethod.PHONE, value=f"+7999000000{n}", bank="Банк")
        )
    with pytest.raises(Conflict):  # больше пяти — нельзя
        await services.game_payments.add_details(coach, card)


async def test_admin_picks_one_of_coach_details(
    services: Services, notifier: FakeNotifier, match_id: int, roster: list[int]
) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach = await ctx_of(services, COACH)
    card = PaymentDetails(method=PaymentMethod.CARD, value="4111111111111111", bank="Т-Банк")
    await services.game_payments.add_details(coach, DETAILS)
    await services.game_payments.add_details(coach, card)
    await services.game_payments.create(
        admin,
        payment(match_id, None, coach.user.id, roster).model_copy(update={"details": card}),
        now=NOW,
    )
    post = notifier.to(CHAT_ID)[-1].text
    assert "4111 1111 1111 1111" in post and "+79991234567" not in post


async def test_all_players_paid_notifies_once(
    services: Services, notifier: FakeNotifier, match_id: int, roster: list[int]
) -> None:
    """«Все» — ответившие «Буду (игрок)»: оба отметили «Оплатил» — тренеру-получателю
    сообщение, один раз; болельщик не в счёт."""
    coach = await ctx_of(services, COACH)
    await services.game_payments.create(
        coach, payment(match_id, None, coach.user.id, roster), now=NOW
    )
    pay_poll = f"poll-{len(notifier.polls)}"
    await services.polls.record_answer(pay_poll, 3, [PAID], now=NOW)  # болельщик
    await services.polls.record_answer(pay_poll, 1, [PAID], now=NOW)
    assert not any("Все игроки оплатили" in m.text for m in notifier.to(COACH))
    await services.polls.record_answer(pay_poll, 2, [PAID], now=NOW)
    paid = [m for m in notifier.to(COACH) if "Все игроки оплатили" in m.text]
    assert len(paid) == 1 and "Оплатили 2 из 2" in paid[0].text
    await services.polls.record_answer(pay_poll, 2, [PAID], now=NOW)  # повторный голос
    assert len([m for m in notifier.to(COACH) if "Все игроки оплатили" in m.text]) == 1
