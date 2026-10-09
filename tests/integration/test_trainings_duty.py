"""ФТ-2 и ФТ-3: расписание → утренний опрос → дежурные → замены."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

from pfbc_bot import callbacks
from pfbc_bot.db.models import DutyAssignment, Training
from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import DutyStatus, SquadRole, TrainingSource
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.trainings import ScheduleCreate, TrainingCreate
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict, PermissionDenied
from pfbc_bot.services.ports import SentPoll
from pfbc_bot.texts import ru

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, TRAININGS_THREAD, ctx_of, join, setup_squad

MSK = "Europe/Moscow"
DAY = date(2026, 10, 5)  # понедельник
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
COACH, CAPTAIN, NEWCOMER = 100, 101, 5
YES, NO = 0, 1


def at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return local_to_utc(day, time(hour, minute), MSK)


async def tg_user_id_map(uow_factory: UowFactory) -> dict[int, int]:
    async with uow_factory() as uow:
        return {u.tg_user_id: u.id for u in await uow.users.list_by_ids(list(range(1, 50)))}


@pytest.fixture
async def team(services: Services, uow_factory: UowFactory) -> dict[str, int]:
    """Тренер, капитан, игроки 1–4 давно в составе, игрок 5 — новичок; тренировки Пн 19:00."""
    squad = await setup_squad(services)
    await join(services, COACH, at=LONG_AGO, role=SquadRole.COACH)
    await join(services, CAPTAIN, at=LONG_AGO, role=SquadRole.CAPTAIN)
    for tg_id in (1, 2, 3, 4):
        await join(services, tg_id, at=LONG_AGO)
    await join(services, NEWCOMER, at=at(9, day=DAY - timedelta(days=3)))

    coach = await ctx_of(services, COACH)
    await services.trainings.add_schedule(
        coach,
        ScheduleCreate(
            squad_id=squad.id,
            weekday=0,
            start_time=time(19, 0),
            duration_minutes=90,
            venue="Зал №5",
        ),
        now=at(8),
    )
    ids = await tg_user_id_map(uow_factory)
    # история дежурств: игрок 1 дежурил неделю назад, игрок 2 — месяц назад, игрок 3 — никогда
    async with uow_factory() as uow:
        for tg_id, days_ago in ((1, 7), (2, 30)):
            past = await uow.trainings.add(
                Training(
                    squad_id=squad.id,
                    starts_at=at(19) - timedelta(days=days_ago),
                    duration_minutes=90,
                    venue="Зал №5",
                    source=TrainingSource.MANUAL,
                )
            )
            await uow.trainings.add_duty(DutyAssignment(training_id=past.id, user_id=ids[tg_id]))
        await uow.commit()
    return {"squad_id": squad.id, **{f"u{k}": v for k, v in ids.items()}}


async def test_full_training_day(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    upcoming = await services.trainings.upcoming(team["squad_id"], now=at(8))
    assert [t.starts_at for t in upcoming] == [at(19), at(19, day=DAY + timedelta(days=7))]
    assert await services.trainings.materialize(now=at(8)) == 0  # идемпотентно

    # утренний опрос — не раньше 09:00 и ровно один раз
    assert await services.polls.publish_training_polls(now=at(8, 59)) == 0
    assert await services.polls.publish_training_polls(now=at(9)) == 1
    assert await services.polls.publish_training_polls(now=at(9, 1)) == 0
    assert notifier.polls == [(CHAT_ID, notifier.polls[0][1], TRAININGS_THREAD)]

    for tg_id in (1, 2, 3, NEWCOMER):
        await services.polls.record_answer("poll-1", tg_id, [YES], now=at(10))
    await services.polls.record_answer("poll-1", 4, [NO], now=at(10))

    # дежурные — за 3 часа до начала: никогда не дежуривший (3) и самый давний (2); новичок исключён
    assert await services.duty.assign_due(now=at(15, 59)) == 0
    assert await services.duty.assign_due(now=at(16)) == 1
    assert await services.duty.assign_due(now=at(16, 1)) == 0
    training_id = upcoming[0].id
    assert await _active_duty(uow_factory, training_id) == {team["u3"], team["u2"]}

    # в группе — одно объявление без кнопок, с упоминаниями дежурных
    announce = [m for m in notifier.to(CHAT_ID) if "Дежурные" in m.text]
    assert len(announce) == 1 and announce[0].thread_id == TRAININGS_THREAD
    assert announce[0].keyboard is None and "tg://user?id=3" in announce[0].text
    # дежурным — в личку с кнопкой «Не могу дежурить»; капитану — сводка с кнопками управления
    assert ru.DUTY_BTN_DECLINE in notifier.to(3)[-1].buttons()
    staff = notifier.to(CAPTAIN)[-1]
    assert ru.DUTY_BTN_MANUAL in staff.buttons()
    assert notifier.to(COACH) == []  # при наличии капитана тренеру сводка не нужна

    # дежурный ответил «Нет» в опросе — автозамена следующим (1): объявление отредактировано,
    # в группе — одно новое сообщение о замене (ответом на объявление, с упоминанием нового),
    # новому — в личку, капитану — уведомление
    group_before = len(notifier.to(CHAT_ID))
    await services.polls.record_answer("poll-1", 3, [NO], now=at(16, 30))
    assert await _active_duty(uow_factory, training_id) == {team["u2"], team["u1"]}
    change = notifier.to(CHAT_ID)[group_before:]
    assert len(change) == 1 and "Замена дежурного" in change[0].text
    assert change[0].reply_to == announce[0].message_id and "tg://user?id=1" in change[0].text
    assert notifier.edits[-1][1] == announce[0].message_id
    assert (
        "tg://user?id=1" in notifier.edits[-1][2] and "tg://user?id=3" not in notifier.edits[-1][2]
    )
    assert "вместо" in notifier.to(1)[-1].text
    assert any("«не приду»" in m.text for m in notifier.to(CAPTAIN))

    # игрок не может менять дежурных
    player = await ctx_of(services, 4)
    assignment_2 = await _assignment_of(uow_factory, training_id, team["u2"])
    with pytest.raises(PermissionDenied):
        await services.duty.confirm_replace(player, assignment_2, now=at(17))
    with pytest.raises(PermissionDenied):
        await services.duty.replace(player, assignment_2, now=at(17))

    # капитан: вопрос-подтверждение ничего не меняет; свободных по очереди нет
    captain = await ctx_of(services, CAPTAIN)
    prompt = await services.duty.confirm_replace(captain, assignment_2, now=at(17))
    assert ru.DUTY_CONFIRM_NO_NEXT in prompt.text
    assert await _active_duty(uow_factory, training_id) == {team["u2"], team["u1"]}
    assert await services.duty.replace(captain, assignment_2, now=at(17)) == ru.DUTY_MANUAL_SENT
    assert "вас заменил" in notifier.to(2)[-1].text  # снятого капитаном предупреждаем
    pick_list = notifier.to(CAPTAIN)[-1]
    pick = callbacks.pack(callbacks.DUTY, callbacks.DUTY_PICK, training_id, team["u5"])
    assert pick in pick_list.buttons().values()

    # ручной выбор — тоже через подтверждение
    pick_prompt = await services.duty.confirm_pick(captain, training_id, team["u5"], now=at(17, 5))
    assert "user5" in pick_prompt.text
    await services.duty.manual_pick(captain, training_id, team["u5"], now=at(17, 5))
    assert await _active_duty(uow_factory, training_id) == {team["u1"], team["u5"]}
    # повторно не прошедшее подтверждение (двойное нажатие) — понятная ошибка, а не дубль
    with pytest.raises(Conflict):
        await services.duty.manual_pick(captain, training_id, team["u5"], now=at(17, 6))

    summary = await services.polls.attendance(await ctx_of(services, COACH), training_id)
    assert len(summary.by_option["Да"]) == 3
    assert len(summary.by_option["Нет"]) == 2


async def test_shortage_notifies_captain(
    services: Services, notifier: FakeNotifier, team: dict[str, int]
) -> None:
    await services.polls.publish_training_polls(now=at(9))
    await services.polls.record_answer("poll-1", 1, [YES], now=at(10))
    await services.duty.assign_due(now=at(16))
    shortage = [m for m in notifier.to(CAPTAIN) if "не хватает дежурных" in m.text]
    assert len(shortage) == 1 and shortage[0].keyboard


async def test_duty_feature_flag_disables_assignment(
    services: Services, notifier: FakeNotifier, team: dict[str, int]
) -> None:
    await services.settings.set_squad_value(
        await ctx_of(services, ADMIN_TG_ID), team["squad_id"], "features.duty", "false"
    )
    await services.polls.publish_training_polls(now=at(9))
    assert await services.duty.assign_due(now=at(16)) == 0


async def test_cancel_after_poll_announces_to_group(
    services: Services, notifier: FakeNotifier, team: dict[str, int]
) -> None:
    await services.polls.publish_training_polls(now=at(9))
    training = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0]
    with pytest.raises(PermissionDenied):
        await services.trainings.cancel(await ctx_of(services, CAPTAIN), training.id, now=at(9))
    await services.trainings.cancel(await ctx_of(services, COACH), training.id, now=at(9))
    assert any("отменена" in m.text for m in notifier.to(CHAT_ID))
    assert await services.duty.assign_due(now=at(16)) == 0


async def _active_duty(uow_factory: UowFactory, training_id: int) -> set[int]:
    async with uow_factory() as uow:
        return {
            a.user_id
            for a in await uow.trainings.list_duties(training_id)
            if a.status == DutyStatus.ASSIGNED
        }


async def _assignment_of(uow_factory: UowFactory, training_id: int, user_id: int) -> int:
    async with uow_factory() as uow:
        assignment = await uow.trainings.active_duty_for_user(training_id, user_id)
        assert assignment is not None
        return assignment.id


async def test_duty_declines_himself_with_confirmation(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    await services.polls.publish_training_polls(now=at(9))
    for tg_id in (1, 2, 3):
        await services.polls.record_answer("poll-1", tg_id, [YES], now=at(10))
    await services.duty.assign_due(now=at(16))
    training_id = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0].id
    assignment_3 = await _assignment_of(uow_factory, training_id, team["u3"])

    # чужое дежурство отменить нельзя
    with pytest.raises(PermissionDenied):
        await services.duty.confirm_decline(await ctx_of(services, 1), assignment_3, now=at(16, 10))

    me = await ctx_of(services, 3)
    prompt = await services.duty.confirm_decline(me, assignment_3, now=at(16, 10))
    assert ru.DUTY_BTN_CONFIRM_DECLINE in {b.text for row in prompt.keyboard for b in row}
    assert await _active_duty(uow_factory, training_id) == {team["u3"], team["u2"]}

    assert await services.duty.decline(me, assignment_3, now=at(16, 11)) == ru.DUTY_DECLINED
    assert await _active_duty(uow_factory, training_id) == {team["u2"], team["u1"]}
    assert any("не может дежурить" in m.text for m in notifier.to(CAPTAIN))
    # ответ в опросе не тронут — в сводке посещаемости игрок по-прежнему «Да»
    summary = await services.polls.attendance(await ctx_of(services, COACH), training_id)
    assert '<a href="tg://user?id=3">user3</a>' in summary.by_option["Да"]
    # повторное нажатие ничего не ломает
    with pytest.raises(Conflict):
        await services.duty.decline(me, assignment_3, now=at(16, 12))


async def test_not_notified_duty_marked_for_captain(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    """Личка закрыта и группа недоступна — капитан видит «не получил уведомление»."""
    await services.polls.publish_training_polls(now=at(9))
    for tg_id in (1, 2, 3):
        await services.polls.record_answer("poll-1", tg_id, [YES], now=at(10))
    notifier.blocked_dm.update({3, CHAT_ID})  # у 3 закрыта личка, в группу отправить нельзя
    await services.duty.assign_due(now=at(16))
    summary = notifier.to(CAPTAIN)[-1].text
    lines = {line for line in summary.splitlines() if line.startswith("•")}
    assert f'• <a href="tg://user?id=3">user3</a>{ru.DUTY_NOT_NOTIFIED_MARK}' in lines
    assert '• <a href="tg://user?id=2">user2</a>' in lines  # личное дошло — пометки нет

    # замена тоже не дошла до нового дежурного — капитан предупреждён
    notifier.blocked_dm.add(1)
    await services.polls.record_answer("poll-1", 2, [NO], now=at(16, 30))
    assert (
        '<a href="tg://user?id=1">user1</a> не получил(а) уведомление'
        in notifier.to(CAPTAIN)[-1].text
    )


async def test_manager_prompts_list_assigned_duties(
    services: Services, notifier: FakeNotifier, team: dict[str, int]
) -> None:
    captain = await ctx_of(services, CAPTAIN)
    # прошедших тренировок с дежурными за 7 дней нет (в фикстуре — 7 и 30 дней назад)
    empty = await services.duty.manager_prompts(captain, team["squad_id"], now=at(19, 1))
    assert len(empty) == 1 and "назначаются автоматически за 3 ч" in empty[0].text
    await services.polls.publish_training_polls(now=at(9))
    await services.polls.record_answer("poll-1", 1, [YES], now=at(10))
    await services.duty.assign_due(now=at(16))
    # сначала ближайшая тренировка, затем прошедшая неделю назад — с кнопкой исправления
    prompts = await services.duty.manager_prompts(captain, team["squad_id"], now=at(16, 5))
    assert len(prompts) == 2 and "user1" in prompts[0].text
    assert ru.DUTY_BTN_MANUAL in {b.text for row in prompts[0].keyboard for b in row}
    assert "Дежурили" in prompts[1].text
    with pytest.raises(PermissionDenied):
        await services.duty.manager_prompts(await ctx_of(services, 1), team["squad_id"])


async def _statuses(uow_factory: UowFactory, training_id: int) -> dict[int, DutyStatus]:
    async with uow_factory() as uow:
        return {a.user_id: a.status for a in await uow.trainings.list_duties(training_id)}


async def _last_duty(uow_factory: UowFactory, team: dict[str, int]) -> dict[int, datetime]:
    async with uow_factory() as uow:
        return await uow.trainings.last_duty_at(
            team["squad_id"], [team[f"u{i}"] for i in (1, 2, 3, 4)]
        )


async def _assign_today(
    services: Services, uow_factory: UowFactory, team: dict[str, int]
) -> tuple[int, int]:
    """Опрос, «Да» от 1–3, назначение дежурных (3 и 2). Возвращает тренировку и назначение 3."""
    training_id = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0].id
    await services.polls.publish_training_polls(now=at(9))
    for tg_id in (1, 2, 3):
        await services.polls.record_answer("poll-1", tg_id, [YES], now=at(10))
    await services.duty.assign_due(now=at(16))
    assert await _active_duty(uow_factory, training_id) == {team["u3"], team["u2"]}
    return training_id, await _assignment_of(uow_factory, training_id, team["u3"])


async def test_duty_done_after_training_starts(
    services: Services, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    assert await services.duty.complete_due(now=at(8)) == 2  # история из фикстуры
    training_id, assignment_3 = await _assign_today(services, uow_factory, team)
    await services.duty.decline(await ctx_of(services, 3), assignment_3, now=at(16, 10))

    assert await services.duty.complete_due(now=at(18, 59)) == 0
    assert await services.duty.complete_due(now=at(19)) == 2
    assert await services.duty.complete_due(now=at(19, 5)) == 0
    assert await _statuses(uow_factory, training_id) == {
        team["u3"]: DutyStatus.REPLACED,
        team["u2"]: DutyStatus.DONE,
        team["u1"]: DutyStatus.DONE,
    }


async def test_cancelled_training_duty_not_counted(
    services: Services, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    training_id, assignment_3 = await _assign_today(services, uow_factory, team)
    await services.trainings.cancel(await ctx_of(services, COACH), training_id, now=at(17))

    assert set((await _statuses(uow_factory, training_id)).values()) == {DutyStatus.CANCELLED}
    await services.duty.complete_due(now=at(19))
    assert set((await _statuses(uow_factory, training_id)).values()) == {DutyStatus.CANCELLED}
    last = await _last_duty(uow_factory, team)
    assert team["u3"] not in last  # отменённая тренировка не сдвигает игрока в очереди
    assert last[team["u2"]] == at(19) - timedelta(days=30)
    with pytest.raises(Conflict):
        await services.duty.fix_pick_list(await ctx_of(services, CAPTAIN), assignment_3, now=at(20))


async def test_fix_past_duty(
    services: Services, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    training_id, assignment_3 = await _assign_today(services, uow_factory, team)
    assignment_2 = await _assignment_of(uow_factory, training_id, team["u2"])
    captain = await ctx_of(services, CAPTAIN)

    # до начала тренировки исправлять нечего — для этого есть замена
    with pytest.raises(Conflict):
        await services.duty.fix_pick_list(captain, assignment_3, now=at(18))
    await services.duty.complete_due(now=at(19))

    # /duty после тренировки: блок прошедшей тренировки с кнопками «Исправить»
    prompts = await services.duty.manager_prompts(captain, team["squad_id"], now=at(20))
    fix_3 = callbacks.pack(callbacks.DUTY, callbacks.DUTY_FIX, assignment_3)
    assert any(b.callback_data == fix_3 for p in prompts for row in p.keyboard for b in row)

    # игрок исправлять не может
    with pytest.raises(PermissionDenied):
        await services.duty.fix(await ctx_of(services, 4), assignment_3, team["u4"], now=at(20))

    # список — все активные, кроме уже засчитанных; засчитанного выбрать нельзя
    pick_list = await services.duty.fix_pick_list(captain, assignment_3, now=at(20))
    picks = {b.callback_data for row in pick_list.keyboard for b in row}
    assert (
        callbacks.pack(callbacks.DUTY, callbacks.DUTY_FIX_PICK, assignment_3, team["u4"]) in picks
    )
    assert (
        callbacks.pack(callbacks.DUTY, callbacks.DUTY_FIX_PICK, assignment_3, team["u2"])
        not in picks
    )
    with pytest.raises(Conflict):
        await services.duty.confirm_fix(captain, assignment_3, team["u2"], now=at(20))

    # вопрос-подтверждение ничего не меняет
    prompt = await services.duty.confirm_fix(captain, assignment_3, team["u4"], now=at(20))
    assert "user4" in prompt.text and "user3" in prompt.text
    assert (await _statuses(uow_factory, training_id))[team["u3"]] == DutyStatus.DONE

    # через 7 дней исправить уже нельзя
    with pytest.raises(Conflict):
        await services.duty.fix(captain, assignment_3, team["u4"], now=at(20) + timedelta(days=8))

    assert "user4" in await services.duty.fix(captain, assignment_3, team["u4"], now=at(20))
    assert await _statuses(uow_factory, training_id) == {
        team["u3"]: DutyStatus.REPLACED,
        team["u2"]: DutyStatus.DONE,
        team["u4"]: DutyStatus.DONE,
    }
    last = await _last_duty(uow_factory, team)
    assert last[team["u4"]] == at(19) and team["u3"] not in last
    # повторное подтверждение — понятная ошибка, а не дубль
    with pytest.raises(Conflict):
        await services.duty.fix(captain, assignment_3, team["u4"], now=at(20))

    # админ без роли в составе тоже может исправить
    admin = await ctx_of(services, ADMIN_TG_ID)
    await services.duty.fix(admin, assignment_2, team["u1"], now=at(21))
    assert (await _statuses(uow_factory, training_id))[team["u1"]] == DutyStatus.DONE


TUE = DAY + timedelta(days=1)


async def test_early_training_poll_eve_before_and_duty_in_morning(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    """Тренировка до 14:00 включительно: опрос накануне в 21:00, дежурные — утром в 08:00."""
    coach = await ctx_of(services, COACH)
    early = await services.trainings.add_one_off(
        coach,
        TrainingCreate(squad_id=team["squad_id"], starts_at=at(14, day=TUE), venue="Зал №5"),
        now=at(8),
    )
    assert await services.polls.publish_training_polls(now=at(20, 59)) == 0
    assert await services.polls.publish_training_polls(now=at(21)) == 1
    assert "06.10.2026 14:00" in notifier.polls[-1][1]
    for tg_id in (1, 2, 3):
        await services.polls.record_answer("poll-1", tg_id, [YES], now=at(21, 30))

    assert await services.duty.assign_due(now=at(7, 59, day=TUE)) == 0
    assert await services.duty.assign_due(now=at(8, day=TUE)) == 1
    assert await _active_duty(uow_factory, early.id) == {team["u3"], team["u2"]}


async def test_duty_waits_for_poll(
    services: Services, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    """Опроса ещё нет — дежурные не назначаются «никем», а ждут его."""
    assert await services.duty.assign_due(now=at(16)) == 0
    await services.polls.publish_training_polls(now=at(16, 1))
    await services.polls.record_answer("poll-1", 1, [YES], now=at(16, 2))
    assert await services.duty.assign_due(now=at(16, 3)) == 1
    training_id = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0].id
    assert await _active_duty(uow_factory, training_id) == {team["u1"]}


async def test_crash_during_duty_messages_does_not_repeat_announcement(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    await services.polls.publish_training_polls(now=at(9))
    for tg_id in (1, 2, 3):
        await services.polls.record_answer("poll-1", tg_id, [YES], now=at(10))
    notifier.crash_on = {2}  # объявление ушло, личное сообщение дежурному «упало»
    with pytest.raises(RuntimeError):
        await services.duty.assign_due(now=at(16))
    assert await services.duty.assign_due(now=at(16, 1)) == 0
    announcements = [m for m in notifier.to(CHAT_ID) if "Дежурные по залу" in m.text]
    assert len(announcements) == 1
    training_id = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0].id
    assert await _active_duty(uow_factory, training_id) == {team["u3"], team["u2"]}


async def test_concurrent_decline_and_replace_make_one_replacement(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    training_id, assignment_3 = await _assign_today(services, uow_factory, team)
    captain = await ctx_of(services, CAPTAIN)
    results = await asyncio.gather(
        services.polls.record_answer("poll-1", 3, [NO], now=at(16, 30)),
        services.duty.replace(captain, assignment_3, now=at(16, 30)),
        return_exceptions=True,
    )
    assert all(r is None or isinstance(r, str | Conflict) for r in results), results
    assert await _active_duty(uow_factory, training_id) == {team["u2"], team["u1"]}
    statuses = [s for s in (await _statuses(uow_factory, training_id)).values()]
    assert statuses.count(DutyStatus.REPLACED) == 1
    assert len([m for m in notifier.to(CHAT_ID) if "Замена дежурного" in m.text]) == 1


async def test_move_reopens_poll_and_reassigns_duty(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    training_id, _ = await _assign_today(services, uow_factory, team)
    coach = await ctx_of(services, COACH)
    await services.trainings.move(coach, training_id, at(19, day=TUE), now=at(17))

    # в группу — новое сообщение о переносе; старый опрос закрыт; снятым дежурным — в личку
    assert any("перенесена" in m.text for m in notifier.to(CHAT_ID))
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]
    for tg_id in (3, 2):
        assert "дежурство снято" in notifier.to(tg_id)[-1].text
    assert set((await _statuses(uow_factory, training_id)).values()) == {DutyStatus.CANCELLED}

    # ответ в старом опросе ни на что не влияет
    await services.polls.record_answer("poll-1", 1, [NO], now=at(17, 5))

    # новый опрос — по новому времени, дежурные — заново, с новым объявлением
    assert await services.polls.publish_training_polls(now=at(8, 59, day=TUE)) == 0
    assert await services.polls.publish_training_polls(now=at(9, day=TUE)) == 1
    for tg_id in (1, 2, 3):
        await services.polls.record_answer("poll-2", tg_id, [YES], now=at(10, day=TUE))
    assert await services.duty.assign_due(now=at(16, day=TUE)) == 1
    assert await _active_duty(uow_factory, training_id) == {team["u3"], team["u2"]}
    assert len([m for m in notifier.to(CHAT_ID) if "Дежурные по залу" in m.text]) == 2


async def test_cancel_or_move_after_start_is_refused(
    services: Services, notifier: FakeNotifier, team: dict[str, int]
) -> None:
    training_id = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0].id
    coach = await ctx_of(services, COACH)
    with pytest.raises(Conflict):
        await services.trainings.cancel(coach, training_id, now=at(19, 1))
    with pytest.raises(Conflict):
        await services.trainings.move(coach, training_id, at(19, day=TUE), now=at(19, 1))
    assert notifier.to(CHAT_ID) == []


async def test_member_who_left_is_replaced_on_duty(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    training_id, _ = await _assign_today(services, uow_factory, team)
    await services.membership.on_leave(CHAT_ID, 3, removed=False, now=at(16, 30))
    assert await _active_duty(uow_factory, training_id) == {team["u2"], team["u1"]}
    assert any("вышел(а) из группы" in m.text for m in notifier.to(CAPTAIN))
    assert any("Замена дежурного" in m.text for m in notifier.to(CHAT_ID))


async def test_manual_poll_before_scheduler(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    """Капитан запустил опрос раньше времени — планировщик свой уже не публикует."""
    training_id = (await services.trainings.upcoming(team["squad_id"], now=at(8)))[0].id
    await services.polls.start_training_poll(
        await ctx_of(services, CAPTAIN), training_id, now=at(8)
    )
    assert notifier.polls == [(CHAT_ID, notifier.polls[0][1], TRAININGS_THREAD)]
    assert "05.10.2026 19:00" in notifier.polls[0][1]

    assert await services.polls.publish_training_polls(now=at(9)) == 0
    assert len(notifier.polls) == 1

    # ответы в ручном опросе — обычные: по ним назначаются дежурные
    for tg_id in (1, 2, 3):
        await services.polls.record_answer("poll-1", tg_id, [YES], now=at(10))
    assert await services.duty.assign_due(now=at(16)) == 1
    assert await _active_duty(uow_factory, training_id) == {team["u3"], team["u2"]}


async def test_manual_poll_restart_closes_bot_poll(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    """Опрос заново поверх опубликованного ботом: старый закрыт, его ответы не учитываются."""
    training_id = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0].id
    assert await services.polls.publish_training_polls(now=at(9)) == 1
    for tg_id in (1, 2, 3):
        await services.polls.record_answer("poll-1", tg_id, [YES], now=at(9, 30))

    admin = await ctx_of(services, ADMIN_TG_ID)
    await services.polls.start_training_poll(admin, training_id, now=at(10))
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]
    assert len(notifier.polls) == 2
    assert await services.polls.publish_training_polls(now=at(11)) == 0

    await services.polls.record_answer("poll-1", 4, [YES], now=at(10, 5))  # в закрытом опросе
    await services.polls.record_answer("poll-2", 1, [YES], now=at(10, 10))
    assert await services.duty.assign_due(now=at(16)) == 1
    assert await _active_duty(uow_factory, training_id) == {team["u1"]}

    summary = await services.polls.attendance(admin, training_id)
    assert len(summary.by_option["Да"]) == 1


async def test_manual_poll_refused(
    services: Services, notifier: FakeNotifier, team: dict[str, int]
) -> None:
    training_id = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0].id
    with pytest.raises(PermissionDenied):
        await services.polls.start_training_poll(await ctx_of(services, 1), training_id, now=at(9))
    coach = await ctx_of(services, COACH)
    with pytest.raises(Conflict):
        await services.polls.start_training_poll(coach, training_id, now=at(19, 1))
    assert notifier.polls == []


async def test_manual_poll_while_scheduler_sends(
    services: Services, notifier: FakeNotifier, team: dict[str, int]
) -> None:
    """Пока планировщик отправлял опрос, тренер запустил свой — действует ручной."""
    training_id = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0].id
    coach = await ctx_of(services, COACH)
    send_poll = notifier.send_poll

    async def send_and_race(*args: Any, **kwargs: Any) -> SentPoll | None:
        sent = await send_poll(*args, **kwargs)
        if len(notifier.polls) == 1:
            await services.polls.start_training_poll(coach, training_id, now=at(9))
        return sent

    notifier.send_poll = send_and_race  # type: ignore[method-assign]
    assert await services.polls.publish_training_polls(now=at(9)) == 0
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]  # опрос планировщика закрыт
    summary = await services.polls.attendance(coach, training_id)
    assert summary.not_answered  # действующий опрос — ручной (poll-2)
    await services.polls.record_answer("poll-2", 1, [YES], now=at(9, 5))
    summary = await services.polls.attendance(coach, training_id)
    assert len(summary.by_option["Да"]) == 1


async def test_duty_messages_link_people(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    """Имена в сообщениях — ссылки: в личке — на профиль, в группе — упоминание."""
    _, assignment_3 = await _assign_today(services, uow_factory, team)
    captain = await ctx_of(services, CAPTAIN)
    prompt = await services.duty.confirm_replace(captain, assignment_3, now=at(16, 10))
    assert '<a href="tg://user?id=3">user3</a>' in prompt.text  # кого заменить
    assert '<a href="tg://user?id=1">user1</a>' in prompt.text  # следующий по очереди
    await services.duty.replace(captain, assignment_3, now=at(16, 11))
    group = [m.text for m in notifier.to(CHAT_ID) if "Замена дежурного" in m.text][-1]
    assert '<a href="tg://user?id=3">user3</a> → <a href="tg://user?id=1">user1</a>' in group
    assert '<a href="tg://user?id=1">user1</a>' in notifier.to(3)[-1].text  # кто заменил
