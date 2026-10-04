"""ФТ-2 и ФТ-3: расписание → утренний опрос → дежурные → замены."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from pfbc_bot import callbacks
from pfbc_bot.db.models import DutyAssignment, Training
from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import DutyStatus, SquadRole, TrainingSource
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.trainings import ScheduleCreate
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict, PermissionDenied
from pfbc_bot.texts import ru

from .conftest import FakeNotifier
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
    # новому — в личку, капитану — уведомление, новых сообщений в группе нет
    group_before = len(notifier.to(CHAT_ID))
    await services.polls.record_answer("poll-1", 3, [NO], now=at(16, 30))
    assert await _active_duty(uow_factory, training_id) == {team["u2"], team["u1"]}
    assert len(notifier.to(CHAT_ID)) == group_before
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
        team["squad_id"], "features.duty", "false", actor_user_id=None
    )
    await services.polls.publish_training_polls(now=at(9))
    assert await services.duty.assign_due(now=at(16)) == 0


async def test_cancel_after_poll_announces_to_group(
    services: Services, notifier: FakeNotifier, team: dict[str, int]
) -> None:
    await services.polls.publish_training_polls(now=at(9))
    training = (await services.trainings.upcoming(team["squad_id"], now=at(9)))[0]
    with pytest.raises(PermissionDenied):
        await services.trainings.cancel(await ctx_of(services, CAPTAIN), training.id)
    await services.trainings.cancel(await ctx_of(services, COACH), training.id)
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
    assert "user3" in summary.by_option["Да"]
    # повторное нажатие ничего не ломает
    with pytest.raises(Conflict):
        await services.duty.decline(me, assignment_3, now=at(16, 12))


async def test_closed_dm_falls_back_to_one_group_mention(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, team: dict[str, int]
) -> None:
    await services.polls.publish_training_polls(now=at(9))
    for tg_id in (1, 2, 3):
        await services.polls.record_answer("poll-1", tg_id, [YES], now=at(10))
    notifier.blocked_dm.update({1, 3})  # личка закрыта у 1 и 3
    await services.duty.assign_due(now=at(16))
    group = notifier.to(CHAT_ID)
    announce = next(m for m in group if "Дежурные" in m.text)
    # 3 упомянут в объявлении — отдельного сообщения нет
    assert [m for m in group if "вы назначены" in m.text] == []

    await services.polls.record_answer("poll-1", 3, [NO], now=at(16, 30))
    # 1 назначен заменой после объявления, личка закрыта — одно упоминание ответом на объявление
    fallback = [m for m in notifier.to(CHAT_ID) if "вы назначены" in m.text]
    assert len(fallback) == 1
    assert fallback[0].reply_to == announce.message_id and "tg://user?id=1" in fallback[0].text


async def test_manager_prompts_list_assigned_duties(
    services: Services, notifier: FakeNotifier, team: dict[str, int]
) -> None:
    captain = await ctx_of(services, CAPTAIN)
    empty = await services.duty.manager_prompts(captain, team["squad_id"], now=at(9))
    assert len(empty) == 1 and "назначаются автоматически за 3 ч" in empty[0].text
    await services.polls.publish_training_polls(now=at(9))
    await services.polls.record_answer("poll-1", 1, [YES], now=at(10))
    await services.duty.assign_due(now=at(16))
    prompts = await services.duty.manager_prompts(captain, team["squad_id"], now=at(16, 5))
    assert len(prompts) == 1 and "user1" in prompts[0].text
    with pytest.raises(PermissionDenied):
        await services.duty.manager_prompts(await ctx_of(services, 1), team["squad_id"])
