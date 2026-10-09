"""ФТ-9: пересечение события с тренировками, матчами и другими событиями состава."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from pfbc_bot.db.models import Match
from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import EventType, MatchSource, PollType, SquadRole
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.schemas.trainings import TrainingCreate
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict, PermissionDenied
from pfbc_bot.services.events import ScheduleConflictError, format_event_line
from pfbc_bot.texts import ru

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, GAMES_THREAD, ctx_of, join, setup_squad, tg

MSK = "Europe/Moscow"
DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
COACH, CAPTAIN, PLAYER = 100, 101, 1


def at(hour: int, minute: int = 0) -> datetime:
    return local_to_utc(DAY, time(hour, minute), MSK)


def event(squad_id: int, start: datetime, minutes: int = 60, **kw: object) -> EventCreate:
    data: dict[str, object] = {
        "squad_id": squad_id,
        "type": EventType.MEETING,
        "title": "Собрание",
        "starts_at": start,
        "duration_minutes": minutes,
        **kw,
    }
    return EventCreate.model_validate(data)


@pytest.fixture
async def squad_id(services: Services) -> int:
    """Состав с тренировкой 19:00–20:30 в день DAY."""
    squad = await setup_squad(services)
    await join(services, COACH, at=LONG_AGO, role=SquadRole.COACH)
    await join(services, CAPTAIN, at=LONG_AGO, role=SquadRole.CAPTAIN)
    await join(services, PLAYER, at=LONG_AGO)
    await services.trainings.add_one_off(
        await ctx_of(services, COACH),
        TrainingCreate(squad_id=squad.id, starts_at=at(19), duration_minutes=90, venue="Зал №5"),
        now=NOW,
    )
    return squad.id


async def captain(services: Services) -> UserContext:
    """Тот, кто ведёт события: игры, турниры, разборы (по умолчанию — тренер)."""
    return await ctx_of(services, COACH)


async def test_overlap_with_training_requires_confirmation(
    services: Services, squad_id: int
) -> None:
    actor = await captain(services)
    conflicts = await services.events.find_conflicts(actor, squad_id, at(20), 60)
    assert [(c.kind, c.title) for c in conflicts] == [("training", "Тренировка, Зал №5")]

    with pytest.raises(ScheduleConflictError) as exc_info:
        await services.events.create(actor, event(squad_id, at(20)), now=NOW)
    assert "Тренировка, Зал №5" in str(exc_info.value)
    assert await services.events.upcoming([squad_id], now=NOW) == []

    created = await services.events.create(
        actor, event(squad_id, at(20)), confirm_conflicts=True, now=NOW
    )
    assert created.duration_minutes == 60


@pytest.mark.parametrize(
    ("start", "minutes"),
    [((17, 30), 90), ((20, 30), 60)],  # заканчивается ровно в 19:00 / начинается ровно в 20:30
)
async def test_back_to_back_is_not_a_conflict(
    services: Services, squad_id: int, start: tuple[int, int], minutes: int
) -> None:
    actor = await captain(services)
    assert await services.events.find_conflicts(actor, squad_id, at(*start), minutes) == []
    await services.events.create(actor, event(squad_id, at(*start), minutes), now=NOW)


async def test_overlap_with_other_event_and_match(
    services: Services, squad_id: int, uow_factory: UowFactory
) -> None:
    actor = await captain(services)
    await services.events.create(actor, event(squad_id, at(10), 120, title="Сбор"), now=NOW)
    async with uow_factory() as uow:
        uow.session.add(
            Match(
                squad_id=squad_id, opponent="Торнадо", starts_at=at(14), source=MatchSource.MANUAL
            )
        )
        await uow.commit()

    conflicts = await services.events.find_conflicts(actor, squad_id, at(11), 4 * 60)
    assert [(c.kind, c.title) for c in conflicts] == [
        ("event", "Собрание: Сбор"),
        ("match", "Матч с Торнадо"),
    ]


async def test_other_squad_and_deleted_events_do_not_conflict(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    other = await services.squads.bind_chat(admin, -200, "PFBC Юниоры", "Юниоры")
    await services.membership.on_join(-200, tg(300), now=LONG_AGO)
    other_coach = await ctx_of(services, 300)
    await services.squads.set_role(
        admin, other.id, other_coach.user.id, SquadRole.COACH, grant=True
    )
    # у другого состава в это же время своё событие — не конфликт для нашего
    await services.events.create(await ctx_of(services, 300), event(other.id, at(10), 120), now=NOW)

    actor = await captain(services)
    removed = await services.events.create(actor, event(squad_id, at(12), 60), now=NOW)
    await services.events.delete(actor, removed.id, now=NOW)
    assert await services.events.find_conflicts(actor, squad_id, at(10), 180) == []


async def test_players_cannot_check_or_create(services: Services, squad_id: int) -> None:
    player = await ctx_of(services, PLAYER)
    with pytest.raises(PermissionDenied):
        await services.events.find_conflicts(player, squad_id, at(10), 60)
    with pytest.raises(PermissionDenied):
        await services.events.create(player, event(squad_id, at(10)), now=NOW)


async def test_publication_goes_to_matching_topic(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    actor = await captain(services)
    await services.events.create(
        actor,
        event(
            squad_id, at(12), 90, type=EventType.MATCH, title="Товарищеская игра", notify_group=True
        ),
        now=NOW,
    )
    await services.events.create(actor, event(squad_id, at(9), 30, title="Тихое"), now=NOW)

    posts = notifier.to(CHAT_ID)
    assert len(posts) == 1  # без флага публикации пост не делается
    assert posts[0].thread_id == GAMES_THREAD
    assert "Товарищеская игра" in posts[0].text and "12:00–13:30" in posts[0].text


async def test_deleting_upcoming_published_event_notifies_team(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    actor = await captain(services)
    published = await services.events.create(
        actor, event(squad_id, at(12), 90, title="Сбор", notify_group=True), now=NOW
    )
    quiet = await services.events.create(actor, event(squad_id, at(9), 30, title="Тихое"), now=NOW)
    group_before = len(notifier.to(CHAT_ID))

    await services.events.delete(actor, published.id, now=NOW)
    cancelled = notifier.to(CHAT_ID)[group_before:]
    assert len(cancelled) == 1 and "Отменено" in cancelled[0].text and "Сбор" in cancelled[0].text
    assert "Сбор" in notifier.to(COACH)[-1].text

    # неопубликованное — только тренерам и капитанам; прошедшее — без сообщений
    await services.events.delete(actor, quiet.id, now=NOW)
    assert len(notifier.to(CHAT_ID)) == group_before + 1
    assert "Тихое" in notifier.to(COACH)[-1].text
    past = await services.events.create(
        actor, event(squad_id, at(21), 30, title="Позднее"), now=NOW
    )
    sent = len(notifier.messages)
    await services.events.delete(actor, past.id, now=at(22))
    assert len(notifier.messages) == sent


async def test_match_and_review_get_participation_poll(
    services: Services, squad_id: int, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    """Матч и разбор игры: к посту — опрос «Буду / Не смогу»; прочим событиям опрос не нужен."""
    actor = await captain(services)
    match = await services.events.create(
        actor,
        event(squad_id, at(12), 90, type=EventType.MATCH, title="PFBC — Динамо", notify_group=True),
        now=NOW,
    )
    await services.events.create(
        actor,
        event(squad_id, at(17), 60, type=EventType.REVIEW, title="Разбор", notify_group=True),
        now=NOW,
    )
    await services.events.create(
        actor, event(squad_id, at(9), 30, title="Собрание", notify_group=True), now=NOW
    )
    await services.events.create(
        actor, event(squad_id, at(21), 30, type=EventType.MATCH, title="Тихий"), now=NOW
    )

    assert [(chat, thread) for chat, _, thread in notifier.polls] == [
        (CHAT_ID, GAMES_THREAD),
        (CHAT_ID, GAMES_THREAD),
    ]
    assert "Матч: PFBC — Динамо, пн 12.10.2026 12:00. Будешь?" in notifier.polls[0][1]
    assert "Разбор игры: Разбор" in notifier.polls[1][1]

    # ответ участника сохраняется
    await services.polls.record_answer("poll-1", PLAYER, [0], now=NOW)
    async with uow_factory() as uow:
        poll = await uow.polls.get_for_ref(PollType.MATCH, "event", match.id)
        assert poll is not None and poll.options == [
            "Буду (тренер)",
            "Буду (игрок)",
            "Буду (болельщик)",
            "Не буду",
        ]
        assert list((await uow.polls.answers(poll.id)).values()) == [[0]]

    # удаление события закрывает опрос
    await services.events.delete(actor, match.id, now=NOW)
    assert notifier.stopped_polls == [(CHAT_ID, 1001)]
    async with uow_factory() as uow:
        assert await uow.polls.get_for_ref(PollType.MATCH, "event", match.id) is None


async def test_tournament_poll_and_match_links(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    """Турнир — свой опрос «Участвую / Не смогу»; матчи привязывает к нему только админ."""
    actor = await captain(services)
    tournament = await services.events.create(
        actor,
        event(
            squad_id,
            at(10),
            60,
            type=EventType.TOURNAMENT,
            title="Кубок",
            notify_group=True,
            duration_days=1,
        ),
        now=NOW,
    )
    match = await services.events.create(
        actor, event(squad_id, at(12), 90, type=EventType.MATCH, title="PFBC — Динамо"), now=NOW
    )
    assert notifier.polls[0][1] == "🏆 Турнир: Кубок, 12.10.2026. Участвуешь?"

    with pytest.raises(PermissionDenied):  # капитан с турнирами не работает
        await services.events.link_match(
            await ctx_of(services, CAPTAIN), tournament.id, match.id, linked=True
        )

    admin = await ctx_of(services, ADMIN_TG_ID)
    card = await services.events.tournament_card(admin, tournament.id, now=NOW)
    assert [(m.title, m.tournament_id) for m in card.matches] == [("PFBC — Динамо", None)]

    await services.events.link_match(admin, tournament.id, match.id, linked=True)
    card = await services.events.tournament_card(admin, tournament.id, now=NOW)
    assert [m.tournament_id for m in card.matches] == [tournament.id]

    # у матча — один турнир; другой турнир его не видит и привязать не может
    other = await services.events.create(
        actor,
        event(squad_id, at(15), 60, type=EventType.TOURNAMENT, title="Лига", is_season=True),
        now=NOW,
    )
    assert (await services.events.tournament_card(admin, other.id, now=NOW)).matches == []
    with pytest.raises(Conflict):
        await services.events.link_match(admin, other.id, match.id, linked=True)

    await services.events.link_match(admin, tournament.id, match.id, linked=False)
    card = await services.events.tournament_card(admin, tournament.id, now=NOW)
    assert [m.tournament_id for m in card.matches] == [None]
    assert [t.title for t, _, _ in await services.events.tournaments(admin, now=NOW)] == [
        "Кубок",
        "Лига",
    ]


async def test_match_of_other_squad_is_not_linked(
    services: Services, squad_id: int, uow_factory: UowFactory
) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    tournament = await services.events.create(
        admin,
        event(squad_id, at(10), 60, type=EventType.TOURNAMENT, title="Кубок", duration_days=3),
        now=NOW,
    )
    other_squad = await services.squads.bind_chat(admin, -100600, "Юниоры", "Юниоры")
    match = await services.events.create(
        admin, event(other_squad.id, at(12), 90, type=EventType.MATCH, title="Матч"), now=NOW
    )
    with pytest.raises(Conflict):
        await services.events.link_match(admin, tournament.id, match.id, linked=True)


async def test_event_attendance_summary(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    actor = await captain(services)
    review = await services.events.create(
        actor,
        event(squad_id, at(18), 60, type=EventType.REVIEW, title="Разбор", notify_group=True),
        now=NOW,
    )
    await services.polls.record_answer("poll-1", PLAYER, [1], now=NOW)
    await services.polls.record_answer("poll-1", COACH, [0], now=NOW)

    summary = await services.polls.event_attendance(actor, review.id)
    assert {k: len(v) for k, v in summary.by_option.items()} == {"Буду": 1, "Не смогу": 1}
    assert len(summary.not_answered) == 1  # капитан не ответил
    assert [e.id for e in await services.events.with_polls(squad_id, now=NOW)] == [review.id]

    # сводки по умолчанию видят и игроки
    player_view = await services.polls.event_attendance(await ctx_of(services, PLAYER), review.id)
    assert player_view.by_option == summary.by_option


async def test_review_default_is_hour_before_first_training_of_week(
    services: Services, squad_id: int
) -> None:
    # тренировка состава — пн 12.10 19:00 (фикстура); NOW — четверг 01.10
    default = await services.events.review_default_at(squad_id, now=NOW)
    assert default == at(18)


async def test_season_tournament_without_start_date(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    """Сезонный турнир без даты старта: создаётся, публикуется с опросом, не мешает событиям."""
    actor = await captain(services)
    league = await services.events.create(
        actor,
        EventCreate(
            squad_id=squad_id,
            type=EventType.TOURNAMENT,
            title="ABL",
            is_season=True,
            notify_group=True,
        ),
        now=NOW,
    )
    assert league.starts_at is None and league.ends_at is None
    assert notifier.polls[0][1] == "🏆 Турнир: ABL, сезон, дата старта уточняется. Участвуешь?"
    assert "сезон, дата старта уточняется" in notifier.to(CHAT_ID)[0].text

    # многодневный турнир, начавшийся раньше (сезон уже идёт), — тоже можно
    cup = await services.events.create(
        actor,
        event(squad_id, at(0), type=EventType.TOURNAMENT, title="Кубок", duration_days=3),
        now=at(12),
    )
    assert cup.ends_at is not None
    # турнир — не пересечение: собрание в дни турнира создаётся без предупреждения
    await services.events.create(actor, event(squad_id, at(10), 60), now=NOW)

    admin = await ctx_of(services, ADMIN_TG_ID)
    titles = [t.title for t, _, _ in await services.events.tournaments(admin, now=NOW)]
    assert titles == ["Кубок", "ABL"]  # без даты — в конце
    assert [e.title for e in await services.events.with_polls(squad_id, now=NOW)] == ["ABL"]


async def test_match_shows_its_tournament(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    actor = await captain(services)
    admin = await ctx_of(services, ADMIN_TG_ID)
    cup = await services.events.create(
        actor,
        event(squad_id, at(9), type=EventType.TOURNAMENT, title="Кубок", duration_days=2),
        now=NOW,
    )
    match = await services.events.create(
        actor,
        event(squad_id, at(12), 90, type=EventType.MATCH, title="PFBC — Динамо", notify_group=True),
        now=NOW,
    )
    await services.events.link_match(admin, cup.id, match.id, linked=True)

    [(dto, _, _)] = [
        x for x in await services.events.upcoming([squad_id], now=NOW) if x[0].id == match.id
    ]
    assert dto.tournament_title == "Кубок"
    assert "🏆 Кубок" in format_event_line(dto, "Взрослые", MSK)

    await services.events.send_due_reminders(now=at(9))  # в день матча
    assert any("🏆 Турнир: Кубок" in m.text and "Динамо" in m.text for m in notifier.to(COACH))
    summary = await services.polls.event_attendance(actor, match.id)
    assert summary.event.tournament_title == "Кубок"


async def test_review_offered_after_match(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    """Матч закончился — тренеру предложение разбора; согласился — разбор с опросом."""
    actor = await captain(services)
    match = await services.events.create(
        actor, event(squad_id, at(12), 90, type=EventType.MATCH, title="PFBC — Динамо"), now=NOW
    )
    assert await services.events.offer_reviews(now=at(13, 29)) == 0  # матч ещё идёт
    assert await services.events.offer_reviews(now=at(13, 30)) == 1
    assert await services.events.offer_reviews(now=at(13, 35)) == 0  # только один раз

    offer = notifier.to(COACH)[-1]
    assert "Назначить разбор игры?" in offer.text and "Динамо" in offer.text
    assert offer.buttons()[ru.REVIEW_BTN_YES] == f"review:yes:{match.id}"
    # разбор — дело тренера: капитан предложение не получает
    assert not any("Назначить разбор" in m.text for m in notifier.to(CAPTAIN))

    coach = await ctx_of(services, COACH)
    proposal = await services.events.review_proposal(coach, match.id, now=at(14))
    assert proposal.default_at == at(18)  # за час до тренировки 19:00 в тот же день
    review = await services.events.create_review(coach, match.id, at(18), now=at(14))
    assert review.type == EventType.REVIEW and review.title == "Разбор игры: PFBC — Динамо"
    assert "Разбор игры: Разбор игры: PFBC — Динамо" in notifier.polls[-1][1]

    with pytest.raises(Conflict):  # второй тренер или двойное нажатие
        await services.events.create_review(actor, match.id, at(18), now=at(14))
    with pytest.raises(PermissionDenied):
        await services.events.review_proposal(await ctx_of(services, PLAYER), match.id)


async def test_review_offer_goes_to_admins_without_coach(
    services: Services, squad_id: int, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach_user = (await ctx_of(services, COACH)).user.id
    await services.squads.set_role(admin, squad_id, coach_user, SquadRole.COACH, grant=False)
    await services.events.create(
        admin, event(squad_id, at(12), 90, type=EventType.MATCH, title="Матч"), now=NOW
    )
    assert await services.events.offer_reviews(now=at(14)) == 1
    # тренера нет — админам (капитану разбор не предлагается)
    assert "Назначить разбор игры?" in notifier.to(ADMIN_TG_ID)[-1].text
    assert not any("Назначить разбор" in m.text for m in notifier.to(CAPTAIN))

    await services.settings.set_squad_value(
        await ctx_of(services, ADMIN_TG_ID), squad_id, "features.review_offers", "false"
    )
    await services.events.create(
        admin, event(squad_id, at(15), 60, type=EventType.MATCH, title="Ещё матч"), now=NOW
    )
    assert await services.events.offer_reviews(now=at(17)) == 0


async def test_match_created_within_tournament(
    services: Services, squad_id: int, notifier: FakeNotifier
) -> None:
    """Матч сразу в рамках турнира: турнир — в посте и опросе; чужой турнир — отказ."""
    actor = await captain(services)
    cup = await services.events.create(
        actor,
        event(squad_id, at(9), type=EventType.TOURNAMENT, title="Кубок", duration_days=2),
        now=NOW,
    )
    await services.events.create(
        actor,
        event(squad_id, at(9), type=EventType.TOURNAMENT, title="Прошлый", duration_days=1),
        now=NOW,
    )
    assert [t.title for t in await services.events.squad_tournaments(actor, squad_id, now=NOW)] == [
        "Кубок",
        "Прошлый",
    ]
    later = at(9) + timedelta(days=1)  # «Прошлый» (1 день) закончился, «Кубок» (2 дня) идёт
    assert [t.id for t in await services.events.squad_tournaments(actor, squad_id, now=later)] == [
        cup.id
    ]

    match = await services.events.create(
        actor,
        event(
            squad_id,
            at(12),
            90,
            type=EventType.MATCH,
            title="PFBC — Динамо",
            tournament_id=cup.id,
            notify_group=True,
        ),
        now=NOW,
    )
    assert match.tournament_id == cup.id and match.tournament_title == "Кубок"
    assert "🏆 Турнир: Кубок" in notifier.to(CHAT_ID)[-1].text
    assert notifier.polls[-1][1].startswith("Матч: PFBC — Динамо (турнир «Кубок»), ")

    admin = await ctx_of(services, ADMIN_TG_ID)
    other_squad = await services.squads.bind_chat(admin, -100600, "Юниоры", "Юниоры")
    with pytest.raises(Conflict):
        await services.events.create(
            admin,
            event(other_squad.id, at(15), type=EventType.MATCH, title="x", tournament_id=cup.id),
            now=NOW,
        )
    with pytest.raises(ValueError, match="только матч"):
        event(squad_id, at(15), type=EventType.MEETING, title="x", tournament_id=cup.id)


async def test_tournament_game_price(services: Services, squad_id: int) -> None:
    actor = await captain(services)
    cup = await services.events.create(
        actor,
        event(squad_id, at(9), type=EventType.TOURNAMENT, title="Кубок", duration_days=1),
        now=NOW,
    )
    assert cup.game_price is None
    updated = await services.events.set_game_price(actor, cup.id, Decimal("12000"))
    assert updated.game_price == Decimal("12000.00")
    assert (await services.events.set_game_price(actor, cup.id, None)).game_price is None
    with pytest.raises(PermissionDenied):
        await services.events.set_game_price(await ctx_of(services, PLAYER), cup.id, None)
    with pytest.raises(ValueError, match="только у турнира"):
        event(squad_id, at(9), type=EventType.MATCH, title="x", game_price=Decimal("1"))
