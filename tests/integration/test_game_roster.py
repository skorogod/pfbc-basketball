"""Заявка на игру: «Буду (игрок)» отметили N → тренеру «Начать сбор»; заявка (до 12) —
источник истины для суммы, напоминаний и «все оплатили»; «Состав на матч» в группе."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete

from pfbc_bot import callbacks
from pfbc_bot.db.models import GamePaymentPlayer
from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import EventType, SquadRole
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.schemas.payments import GamePaymentCreate, PaymentDetails, PaymentMethod
from pfbc_bot.services.container import Services, build_services
from pfbc_bot.services.errors import Conflict, PermissionDenied
from pfbc_bot.texts import ru

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, MATCH_THREAD, ctx_of, join, setup_squad

MSK = "Europe/Moscow"
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
KICKOFF = local_to_utc(date(2026, 10, 18), time(12, 30), MSK)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
COACH, CAPTAIN = 100, 101
PLAYERS = range(1, 15)  # tg 1…14
PLAYER_OPT, NO_OPT = 1, 3
PAID = 0
DETAILS = PaymentDetails(method=PaymentMethod.PHONE, value="+79991234567", bank="Сбербанк")
START = ru.ROSTER_BTN_START


@pytest.fixture
async def match_id(services: Services) -> int:
    """Матч турнира со стоимостью игры 15 000 ₽, опрос участия poll-1; 14 игроков, тренер,
    капитан."""
    squad = await setup_squad(services)
    await join(services, COACH, at=LONG_AGO, role=SquadRole.COACH)
    await join(services, CAPTAIN, at=LONG_AGO, role=SquadRole.CAPTAIN)
    for tg_id in PLAYERS:
        await join(services, tg_id, at=LONG_AGO)
    await join(services, ADMIN_TG_ID, at=LONG_AGO)  # админ — участник группы состава
    admin = await ctx_of(services, ADMIN_TG_ID)
    cup = await services.events.create(
        admin,
        EventCreate(
            squad_id=squad.id,
            type=EventType.TOURNAMENT,
            title="ABL",
            is_season=True,
            game_price=Decimal("15000"),
        ),
        now=NOW,
    )
    match = await services.events.create(
        admin,
        EventCreate(
            squad_id=squad.id,
            type=EventType.MATCH,
            title="PFBC — Динамо",
            starts_at=KICKOFF,
            duration_minutes=120,
            place="ФОК «Москвич»",
            tournament_id=cup.id,
            notify_group=True,
        ),
        now=NOW,
    )
    return match.id


async def vote(services: Services, tg_ids: range | list[int], option: int = PLAYER_OPT) -> None:
    for n, tg_id in enumerate(tg_ids):  # по очереди: время ответа задаёт порядок в заявке
        await services.polls.record_answer(
            "poll-1", tg_id, [option], now=NOW + timedelta(seconds=tg_id + n)
        )


def notices(notifier: FakeNotifier, tg_id: int) -> list[str]:
    return [m.text for m in notifier.to(tg_id) if START in m.buttons()]


async def user_ids(services: Services, tg_ids: range | list[int]) -> list[int]:
    return [(await ctx_of(services, tg_id)).user.id for tg_id in tg_ids]


def payment(match_id: int, collector_id: int, roster: list[int], **kw: object) -> GamePaymentCreate:
    data: dict[str, object] = {
        "match_event_id": match_id,
        "collector_user_id": collector_id,
        "amount": Decimal("1250"),
        "deadline_at": NOW + timedelta(days=1),
        "details": DETAILS,
        "player_ids": roster,
        **kw,
    }
    return GamePaymentCreate.model_validate(data)


async def test_notice_is_edited_in_place(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, match_id: int
) -> None:
    """Сообщение о составе — одно на получателя: дальше число меняется правкой (Telegram о
    правке не уведомляет — тренера не дёргаем)."""
    await vote(services, range(1, 10))
    assert await services.game_payments.send_roster_updates(now=NOW) == 0
    assert notices(notifier, COACH) == []

    await vote(services, [10])  # 10-й — состав набран: тренеру и админу-участнику
    assert await services.game_payments.send_roster_updates(now=NOW) == 1
    [first] = notices(notifier, COACH)
    assert "PFBC — Динамо" in first and "отметили <b>10</b>" in first
    assert len(notices(notifier, ADMIN_TG_ID)) == 1
    assert notices(notifier, CAPTAIN) == []  # сбор денег — не право капитана
    message = notifier.to(COACH)[-1]
    assert message.buttons()[START] == callbacks.pack(
        callbacks.ROSTER, callbacks.ROSTER_START, match_id
    )

    def edited(tg_id: int) -> list[str]:
        own = {m.message_id for m in notifier.to(tg_id)}
        return [text for chat, mid, text in notifier.edits if chat == tg_id and mid in own]

    await vote(services, [11])
    assert await services.game_payments.send_roster_updates(now=NOW + timedelta(minutes=1)) == 1
    assert len(notifier.to(COACH)) == 1  # нового сообщения нет
    assert (
        "отметили <b>11</b>" in edited(COACH)[-1] and "обновлено 13.10 15:01" in edited(COACH)[-1]
    )
    assert len(edited(ADMIN_TG_ID)) == 1

    # 12 и обратно 11 за одну минуту — правки нет
    await vote(services, [12])
    await vote(services, [12], NO_OPT)
    assert await services.game_payments.send_roster_updates(now=NOW + timedelta(minutes=2)) == 0
    assert len(edited(COACH)) == 1

    # меньше порога — то же сообщение, кнопка остаётся, подсказка
    await vote(services, [10, 11], NO_OPT)
    await services.game_payments.send_roster_updates(now=NOW + timedelta(minutes=3))
    assert "отметили <b>9</b>" in edited(COACH)[-1] and "не хватает 1" in edited(COACH)[-1]

    # рестарт worker — без повторов
    restarted = build_services(uow_factory, notifier, [ADMIN_TG_ID])
    assert await restarted.game_payments.send_roster_updates(now=NOW + timedelta(minutes=4)) == 0

    # тренер удалил сообщение — правка не прошла: новое сообщение, дальше правится оно
    notifier.fail_edits = {COACH}
    await vote(services, range(9, 15))  # 14 — больше заявки
    await services.game_payments.send_roster_updates(now=NOW + timedelta(minutes=5))
    [_, again] = notices(notifier, COACH)
    assert "отметили <b>14</b>" in again and "не больше 12" in again
    notifier.fail_edits = set()
    await vote(services, [14], NO_OPT)
    await services.game_payments.send_roster_updates(now=NOW + timedelta(minutes=6))
    assert len(notices(notifier, COACH)) == 2
    last_id = notifier.to(COACH)[-1].message_id
    assert any(mid == last_id and "<b>13</b>" in text for _, mid, text in notifier.edits)


async def test_two_workers_do_not_duplicate_notice(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, match_id: int
) -> None:
    """Второй worker на той же БД: уникальность (опрос, получатель) — по сообщению на
    получателя, а не по два."""
    await vote(services, range(1, 11))
    other = build_services(uow_factory, notifier, [ADMIN_TG_ID])
    await asyncio.gather(
        services.game_payments.send_roster_updates(now=NOW),
        other.game_payments.send_roster_updates(now=NOW),
    )
    assert len(notices(notifier, COACH)) == 1
    assert len(notices(notifier, ADMIN_TG_ID)) == 1


async def test_crash_while_sending_does_not_repeat(
    services: Services, notifier: FakeNotifier, match_id: int
) -> None:
    """Отметка — до отправки: worker упал посреди рассылки — следующий тик не повторяет
    (сообщение скорее не уйдёт, чем уйдёт дважды)."""
    await vote(services, range(1, 11))
    notifier.crash_on = {notifier._attempts + 1}  # первое же уведомление
    with pytest.raises(RuntimeError):
        await services.game_payments.send_roster_updates(now=NOW)
    await services.game_payments.send_roster_updates(now=NOW + timedelta(minutes=1))
    sent = len(notices(notifier, COACH)) + len(notices(notifier, ADMIN_TG_ID))
    assert sent == 0  # не дошло, но и не задвоилось


async def test_threshold_setting(services: Services, notifier: FakeNotifier, match_id: int) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    squad_id = (await services.events.get(match_id)).squad_id
    await services.settings.set_squad_value(admin, squad_id, "game_payment_squad_size", "0")
    await vote(services, PLAYERS)
    assert await services.game_payments.send_roster_updates(now=NOW) == 0
    await services.settings.set_squad_value(admin, squad_id, "game_payment_squad_size", "14")
    assert await services.game_payments.send_roster_updates(now=NOW) == 1


async def test_start_collection_closes_poll_and_buttons(
    services: Services, notifier: FakeNotifier, match_id: int
) -> None:
    await vote(services, range(1, 11))
    await services.game_payments.send_roster_updates(now=NOW)
    coach = await ctx_of(services, COACH)
    with pytest.raises(PermissionDenied):
        await services.game_payments.start_collection(
            await ctx_of(services, CAPTAIN), match_id, now=NOW
        )
    await services.game_payments.start_collection(coach, match_id, now=NOW)
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]  # голосование участия закрыто
    stripped = {(chat, mid) for chat, mid, _ in notifier.edits}
    assert {(m.chat_id, m.message_id) for m in notifier.messages if START in m.buttons()} <= (
        stripped
    )
    # тренер бросил выбор заявки — повторное «Начать сбор» продолжает, опрос не трогается
    await services.game_payments.start_collection(coach, match_id, now=NOW)
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]
    await vote(services, [11])  # голоса после закрытия ни на что не влияют
    assert await services.game_payments.send_roster_updates(now=NOW + timedelta(minutes=1)) == 0


async def test_roster_choice_and_amount(services: Services, match_id: int) -> None:
    await vote(services, PLAYERS)
    choice = await services.game_payments.roster(await ctx_of(services, COACH), match_id)
    voted = [c.user.id for c in choice.candidates if c.voted]
    assert voted == await user_ids(services, PLAYERS)  # по времени ответа
    assert choice.preselected == voted[:12] and choice.max_players == 12
    others = {c.user.id for c in choice.candidates if not c.voted}
    assert (await ctx_of(services, COACH)).user.id in others  # можно добавить из состава
    assert not choice.has_payment

    suggestion = await services.game_payments.suggest_amount(
        await ctx_of(services, COACH), match_id, 11
    )
    assert suggestion is not None and suggestion.per_player == Decimal("1364")  # вверх


async def test_payment_follows_roster_not_poll(
    services: Services, notifier: FakeNotifier, match_id: int
) -> None:
    """14 ответили «Буду (игрок)», в заявке — 11 из них и один не голосовавший: платят ровно
    эти 12."""
    await vote(services, PLAYERS)
    coach = await ctx_of(services, COACH)
    captain = await ctx_of(services, CAPTAIN)
    roster = [*(await user_ids(services, range(1, 12))), captain.user.id]
    with pytest.raises(Conflict):  # больше 12
        await services.game_payments.create(
            coach, payment(match_id, coach.user.id, [*roster, roster[0] + 100]), now=NOW
        )
    with pytest.raises(Conflict):  # не участник состава
        await services.game_payments.create(
            coach, payment(match_id, coach.user.id, [*roster[:11], 9999]), now=NOW
        )
    await services.game_payments.create(coach, payment(match_id, coach.user.id, roster), now=NOW)

    roster_post, post = (m.text for m in notifier.to(CHAT_ID)[-2:])
    assert roster_post.startswith(
        "🏀 <b>Состав на матч</b>\n<b>PFBC — Динамо</b>\n🗓 вс, 18.10 · 12:30\n📍 ФОК «Москвич»"
        "\n🏆 ABL\n\n<b>Игроки (12):</b>\n1. "
    )
    assert "user101" in roster_post and "user12" not in roster_post
    assert notifier.to(CHAT_ID)[-1].thread_id == MATCH_THREAD
    assert "Платят игроки из состава выше (12)" in post

    await services.game_payments.remind_due(now=NOW + timedelta(days=1))
    assert any("Напоминание об оплате" in m.text for m in notifier.to(CAPTAIN))  # без голоса
    for tg_id in (12, 13, 14):  # голосовали, но не в заявке
        assert not any("Напоминание об оплате" in m.text for m in notifier.to(tg_id))

    pay_poll = f"poll-{len(notifier.polls)}"
    for tg_id in [*range(1, 12), 12]:  # 12-й не в заявке — «Оплатил» не в счёт
        await services.polls.record_answer(pay_poll, tg_id, [PAID], now=NOW)
    assert not any("Все игроки оплатили" in m.text for m in notifier.to(COACH))
    await services.polls.record_answer(pay_poll, CAPTAIN, [PAID], now=NOW)
    [all_paid] = [m.text for m in notifier.to(COACH) if "Все игроки оплатили" in m.text]
    assert "Оплатили 12 из 12" in all_paid
    status = await services.game_payments.status(coach, match_id)
    assert status is not None and len(status.paid) == 12 and len(status.others_paid) == 1


async def test_second_coach_cannot_override_started_collection(
    services: Services, notifier: FakeNotifier, match_id: int
) -> None:
    await vote(services, range(1, 11))
    coach = await ctx_of(services, COACH)
    admin = await ctx_of(services, ADMIN_TG_ID)
    roster = await user_ids(services, range(1, 11))
    await services.game_payments.add_details(admin, DETAILS)
    first = await services.game_payments.create(
        coach, payment(match_id, coach.user.id, roster, replace_active=False), now=NOW
    )
    sent_before = len(notifier.messages)
    with pytest.raises(Conflict):
        await services.game_payments.create(
            admin, payment(match_id, admin.user.id, roster, replace_active=False), now=NOW
        )
    assert len(notifier.messages) == sent_before  # проверено до отправки — в группе ничего
    with pytest.raises(Conflict):
        await services.game_payments.start_collection(admin, match_id, now=NOW)
    status = await services.game_payments.status(coach, match_id)
    assert status is not None and status.payment.id == first.id


async def test_legacy_payment_without_roster_uses_poll(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory, match_id: int
) -> None:
    """Сбор, созданный до появления заявки, — игроки по-прежнему из опроса участия."""
    await vote(services, [1, 2])
    coach = await ctx_of(services, COACH)
    created = await services.game_payments.create(
        coach, payment(match_id, coach.user.id, await user_ids(services, [1])), now=NOW
    )
    async with uow_factory() as uow:
        await uow.session.execute(
            delete(GamePaymentPlayer).where(GamePaymentPlayer.payment_id == created.id)
        )
        await uow.commit()
    status = await services.game_payments.status(coach, match_id)
    assert status is not None and len(status.unpaid) == 2
