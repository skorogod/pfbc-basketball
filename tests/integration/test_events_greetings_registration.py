"""ФТ-9 (напоминания о событиях), ФТ-6 (дни рождения), ФТ-1 (регистрация)."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import EventType, SquadRole, UserStatus
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.schemas.users import RegistrationData
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import PermissionDenied

from .conftest import FakeNotifier
from .helpers import CHAT_ID, GAMES_THREAD, GREETINGS_THREAD, ctx_of, join, setup_squad

MSK = "Europe/Moscow"
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
COACH, CAPTAIN = 100, 101


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return local_to_utc(day, time(hour, minute), MSK)


async def test_event_reminders_3_1_0_days_exactly_once(
    services: Services, notifier: FakeNotifier
) -> None:
    squad = await setup_squad(services)
    await join(services, COACH, at=LONG_AGO, role=SquadRole.COACH)
    await join(services, CAPTAIN, at=LONG_AGO, role=SquadRole.CAPTAIN)
    await join(services, 1, at=LONG_AGO)
    event_day = date(2026, 10, 10)

    with pytest.raises(PermissionDenied):
        await services.events.create(
            await ctx_of(services, 1),
            EventCreate(
                squad_id=squad.id, type=EventType.MEETING, title="x", starts_at=at(event_day, 18)
            ),
            now=at(date(2026, 10, 1), 12),
        )
    await services.events.create(
        await ctx_of(services, CAPTAIN),
        EventCreate(
            squad_id=squad.id,
            type=EventType.TOURNAMENT,
            title="Турнир памяти",
            starts_at=at(event_day, 18),
            place="ФОК",
            notify_group=True,
        ),
        now=at(date(2026, 10, 1), 12),
    )

    sent_days = []
    for day_offset in range(6, -1, -1):  # с 4 октября по 10 октября, тик в 09:00 и повтор в 09:05
        day = event_day - timedelta(days=day_offset)
        for minute in (0, 5):
            if await services.events.send_due_reminders(now=at(day, 9, minute)):
                sent_days.append(day_offset)
    assert sent_days == [3, 1, 0]
    assert len(notifier.to(COACH)) == 3 and len(notifier.to(CAPTAIN)) == 3
    assert len(notifier.to(1)) == 0  # игрокам — только через группу
    # в группе: пост о турнире при создании + 3 напоминания, всё в топике «Игры»
    group = notifier.to(CHAT_ID)
    assert len(group) == 4 and all(m.thread_id == GAMES_THREAD for m in group)
    assert "Турнир памяти" in group[0].text and "18:00–19:30" in group[0].text
    assert "завтра" in notifier.to(COACH)[1].text

    events = await services.events.upcoming([squad.id], now=at(date(2026, 10, 1), 12))
    assert [e.title for e, _, _ in events] == ["Турнир памяти"]


async def test_birthday_greeting_once_in_greetings_topic(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    await setup_squad(services)
    await join(services, 1, at=LONG_AGO)
    await join(services, 2, at=LONG_AGO)
    today = date(2026, 10, 5)
    async with uow_factory() as uow:
        for tg_id, birth in ((1, date(2000, 10, 5)), (2, date(2000, 10, 6))):
            user = await uow.users.get_by_tg_id(tg_id)
            assert user is not None
            user.birth_date, user.status = birth, UserStatus.ACTIVE
        await uow.commit()

    assert await services.birthdays.send_due(now=at(today, 9, 59)) == 0
    assert await services.birthdays.send_due(now=at(today, 10)) == 1
    assert await services.birthdays.send_due(now=at(today, 10, 5)) == 0
    greeting = notifier.to(CHAT_ID)[-1]
    assert greeting.thread_id == GREETINGS_THREAD and "tg://user?id=1" in greeting.text


async def test_crash_during_birthdays_does_not_repeat_greetings(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    await setup_squad(services)
    for tg_id in (1, 2, 3):
        await join(services, tg_id, at=LONG_AGO)
    today = date(2026, 10, 5)
    async with uow_factory() as uow:
        for tg_id in (1, 2, 3):
            user = await uow.users.get_by_tg_id(tg_id)
            assert user is not None
            user.birth_date, user.status = date(2000, 10, 5), UserStatus.ACTIVE
        await uow.commit()

    notifier.crash_on = {2}  # первое поздравление ушло, на втором — сбой
    with pytest.raises(RuntimeError):
        await services.birthdays.send_due(now=at(today, 10))
    await services.birthdays.send_due(now=at(today, 10, 5))
    greeted = [m.text for m in notifier.to(CHAT_ID)]
    # каждый — не больше одного раза (упавшее не повторяется: лучше пропустить, чем задвоить)
    for tg_id in (1, 2, 3):
        assert sum(f'tg://user?id={tg_id}"' in t for t in greeted) <= 1
    assert len(greeted) == 2


async def test_staff_get_birthday_heads_up_7_3_1_days_before(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    await setup_squad(services)
    await join(services, COACH, at=LONG_AGO, role=SquadRole.COACH)
    await join(services, CAPTAIN, at=LONG_AGO, role=SquadRole.CAPTAIN)
    await join(services, 1, at=LONG_AGO)
    await join(services, 2, at=LONG_AGO)
    birthday = date(2026, 10, 12)
    births = {1: date(2000, 10, 12), 2: date(2001, 10, 12), CAPTAIN: date(1990, 10, 12)}
    async with uow_factory() as uow:
        for tg_id, birth in births.items():
            user = await uow.users.get_by_tg_id(tg_id)
            assert user is not None
            user.birth_date, user.status = birth, UserStatus.ACTIVE
        await uow.commit()

    heads_up_days = []
    for days_before in range(10, -1, -1):
        day = birthday - timedelta(days=days_before)
        for hour, minute in ((9, 59), (10, 0), (10, 5)):  # до времени, в срок и повторный тик
            if await services.birthdays.send_staff_reminders(now=at(day, hour, minute)):
                heads_up_days.append(days_before)
    assert heads_up_days == [7, 3, 1]

    # тренеру — одна сводка в день о трёх именинниках; капитану — без его собственного ДР
    coach_msgs = notifier.to(COACH)
    assert len(coach_msgs) == 3
    assert all(m.text.count("•") == 3 for m in coach_msgs)
    assert "через 7 дн." in coach_msgs[0].text and "завтра" in coach_msgs[-1].text
    captain_msgs = notifier.to(CAPTAIN)
    assert len(captain_msgs) == 3
    assert all(f"id={CAPTAIN}" not in m.text and m.text.count("•") == 2 for m in captain_msgs)
    # игрокам и в группу предупреждения не уходят; в сам день — только поздравление в топике
    assert notifier.to(1) == [] and notifier.to(CHAT_ID) == []
    await services.birthdays.send_due(now=at(birthday, 10))
    assert {m.thread_id for m in notifier.to(CHAT_ID)} == {GREETINGS_THREAD}
    assert len(notifier.to(COACH)) == 3


async def test_registration_and_personal_data_deletion(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    await setup_squad(services)
    t0 = at(date(2026, 10, 1), 12)
    await join(services, 1, at=t0)
    await join(services, 2, at=t0)
    ctx2 = await ctx_of(services, 2)
    user = await services.registration.complete(
        ctx2.user.id,
        RegistrationData(
            full_name="Петров Пётр",
            birth_date=date(2001, 4, 1),
            mtgame_url="https://mtgame.ru/player/1",
            pd_consent=True,
        ),
        now=t0,
    )
    assert user.is_registered and user.status == UserStatus.ACTIVE

    assert notifier.to(CHAT_ID) == []  # ни приветствий, ни напоминаний в группе

    await services.registration.delete_personal_data(ctx2.user.id)
    profile = await services.registration.profile(ctx2.user.id)
    assert profile.user.full_name is None and profile.user.birth_date is None
    assert profile.user.status == UserStatus.UNREGISTERED
    assert [s.name for s in profile.squads] == ["Взрослые"]
