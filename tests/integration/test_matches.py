"""ФТ-4: матчи ABL из mtgame.ru — пост и опрос в топике match, перенос, смена зала."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from pfbc_bot.db.models import Event, Match
from pfbc_bot.domain.enums import EventType, PollType, SquadRole
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.schemas.matches import ExternalGame
from pfbc_bot.services.container import Services, build_services
from pfbc_bot.services.errors import Conflict, PermissionDenied
from pfbc_bot.services.events import EVENT_REF
from pfbc_bot.services.ports import MatchSourceError
from pfbc_bot.texts import ru

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, MATCH_THREAD, ctx_of, join, setup_squad

TEAM_ID = 9410
COACH, PLAYER = 100, 1
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)  # 12:30 МСК
VENUE = "Волгоградский пр-т., 46/15с13"
DRAFT = ExternalGame(
    external_id="mtgame:163743",
    home="PFBC",
    away="Hungry dogs OLD",
    opponent="Hungry dogs OLD",
    tournament="Москвич",
    tour=7,
)
SCHEDULED = replace(DRAFT, starts_at=KICKOFF, venue=VENUE)


@dataclass
class FakeSource:
    games: list[ExternalGame] = field(default_factory=list)
    error: str | None = None
    calls: list[int] = field(default_factory=list)

    async def upcoming_games(self, team_id: int) -> list[ExternalGame]:
        self.calls.append(team_id)
        if self.error is not None:
            raise MatchSourceError(self.error)
        return list(self.games)


@pytest.fixture
def source() -> FakeSource:
    return FakeSource()


@pytest.fixture
def services(uow_factory: UowFactory, notifier: FakeNotifier, source: FakeSource) -> Services:
    return build_services(uow_factory, notifier, [ADMIN_TG_ID], source)


@pytest.fixture
async def squad_id(services: Services) -> int:
    squad = await setup_squad(services)
    admin = await ctx_of(services, ADMIN_TG_ID)
    await services.settings.set_squad_value(admin, squad.id, "mtgame_team_id", str(TEAM_ID))
    return squad.id


async def monitor(services: Services, squad_id: int) -> None:
    """Следить за найденными матчами (переносы, отмены) — источник опрашивается всегда."""
    admin = await ctx_of(services, ADMIN_TG_ID)
    await services.settings.set_squad_value(admin, squad_id, "features.match_monitoring", "true")


async def match_events(uow_factory: UowFactory) -> list[Event]:
    async with uow_factory() as uow:
        stmt = select(Event).where(Event.type == EventType.MATCH, Event.deleted_at.is_(None))
        return list(await uow.session.scalars(stmt))


async def active_poll_message(uow_factory: UowFactory, event_id: int) -> int | None:
    async with uow_factory() as uow:
        poll = await uow.polls.get_for_ref(PollType.MATCH, EVENT_REF, event_id)
        return poll.message_id if poll is not None else None


async def test_new_game_published_once_date_known(
    services: Services,
    notifier: FakeNotifier,
    source: FakeSource,
    uow_factory: UowFactory,
    squad_id: int,
) -> None:
    source.games = [DRAFT]
    assert await services.matches.sync(now=NOW) == 0
    assert source.calls == [TEAM_ID]
    assert notifier.messages == [] and await match_events(uow_factory) == []

    # во вторник вечером лига назначила время и зал
    source.games = [SCHEDULED]
    assert await services.matches.sync(now=NOW) == 1
    [event] = await match_events(uow_factory)
    assert event.title == "PFBC — Hungry dogs OLD"
    assert (event.starts_at, event.place) == (KICKOFF, VENUE)
    assert event.description == "Москвич, тур 7" and event.notify_group
    [post] = notifier.to(CHAT_ID)
    assert post.thread_id == MATCH_THREAD
    assert "PFBC — Hungry dogs OLD" in post.text and VENUE in post.text
    [(chat, question, thread)] = notifier.polls
    assert (chat, thread) == (CHAT_ID, MATCH_THREAD)
    assert "18.10.2026 12:30" in question
    async with uow_factory() as uow:
        match = await uow.matches.get_by_external(squad_id, "mtgame:163743")
        assert match is not None and event.match_id == match.id
        assert (match.opponent, match.stage, match.division) == (
            "Hungry dogs OLD",
            "тур 7",
            "Москвич",
        )

    # игра найдена — поиск закрыт: mtgame больше не опрашивается
    assert await services.matches.sync(now=NOW + timedelta(minutes=30)) == 0
    assert source.calls == [TEAM_ID, TEAM_ID]
    assert len(notifier.messages) == 1 and len(notifier.polls) == 1

    # с мониторингом опрашивается и дальше — без дублей
    await monitor(services, squad_id)
    assert await services.matches.sync(now=NOW + timedelta(hours=1)) == 0
    assert len(source.calls) == 3 and len(notifier.polls) == 1


async def test_moved_game_reposts_poll(
    services: Services,
    notifier: FakeNotifier,
    source: FakeSource,
    uow_factory: UowFactory,
    squad_id: int,
) -> None:
    await monitor(services, squad_id)
    source.games = [SCHEDULED]
    await services.matches.sync(now=NOW)
    [event] = await match_events(uow_factory)
    old_poll = await active_poll_message(uow_factory, event.id)

    later = KICKOFF + timedelta(hours=2)
    source.games = [replace(SCHEDULED, starts_at=later)]
    assert await services.matches.sync(now=NOW) == 1
    [moved] = await match_events(uow_factory)
    assert moved.starts_at == later
    assert notifier.stopped_polls == [(CHAT_ID, old_poll)]
    new_poll = await active_poll_message(uow_factory, event.id)
    assert new_poll is not None and new_poll != old_poll
    announce = notifier.to(CHAT_ID)[-1]
    assert announce.text.startswith("🔁") and "14:30" in announce.text
    assert announce.thread_id == MATCH_THREAD
    async with uow_factory() as uow:
        match = await uow.matches.get_by_external(squad_id, SCHEDULED.external_id)
        assert match is not None and match.starts_at == later


async def test_new_venue_announced_without_new_poll(
    services: Services,
    notifier: FakeNotifier,
    source: FakeSource,
    uow_factory: UowFactory,
    squad_id: int,
) -> None:
    await monitor(services, squad_id)
    source.games = [replace(SCHEDULED, venue=None)]
    await services.matches.sync(now=NOW)
    source.games = [SCHEDULED]
    assert await services.matches.sync(now=NOW) == 1
    [event] = await match_events(uow_factory)
    assert event.place == VENUE
    assert len(notifier.polls) == 1 and notifier.stopped_polls == []
    text = notifier.to(CHAT_ID)[-1].text
    assert text.startswith("📍") and VENUE in text


async def test_manual_match_is_linked_not_duplicated(
    services: Services,
    notifier: FakeNotifier,
    source: FakeSource,
    uow_factory: UowFactory,
    squad_id: int,
) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    manual = await services.events.create(
        admin,
        EventCreate(
            squad_id=squad_id,
            type=EventType.MATCH,
            title="Матч с Hungry dogs",
            starts_at=KICKOFF + timedelta(hours=1),  # тренер ошибся со временем
            duration_minutes=120,
        ),
        now=NOW,
    )
    source.games = [SCHEDULED]
    assert await services.matches.sync(now=NOW) == 1
    [event] = await match_events(uow_factory)
    assert event.id == manual.id and event.match_id is not None
    assert notifier.messages == [] and notifier.polls == []


async def test_linked_to_journal_tournament(
    services: Services,
    notifier: FakeNotifier,
    source: FakeSource,
    uow_factory: UowFactory,
    squad_id: int,
) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    tournament = await services.events.create(
        admin,
        EventCreate(
            squad_id=squad_id, type=EventType.TOURNAMENT, title="ABL Москвич", is_season=True
        ),
        now=NOW,
    )
    source.games = [SCHEDULED]
    await services.matches.sync(now=NOW)
    [event] = await match_events(uow_factory)
    assert event.tournament_id == tournament.id and event.description == "Тур 7"
    [(_, question, _)] = notifier.polls
    assert "турнир «ABL Москвич»" in question


async def test_deleted_match_not_restored(
    services: Services,
    notifier: FakeNotifier,
    source: FakeSource,
    uow_factory: UowFactory,
    squad_id: int,
) -> None:
    await monitor(services, squad_id)
    source.games = [SCHEDULED]
    await services.matches.sync(now=NOW)
    [event] = await match_events(uow_factory)
    await services.events.delete(await ctx_of(services, ADMIN_TG_ID), event.id, now=NOW)
    source.games = [replace(SCHEDULED, starts_at=KICKOFF + timedelta(hours=1))]
    assert await services.matches.sync(now=NOW) == 0
    assert await match_events(uow_factory) == []


async def test_squad_without_team_not_polled(
    services: Services, source: FakeSource, uow_factory: UowFactory
) -> None:
    await setup_squad(services)
    source.games = [SCHEDULED]
    assert await services.matches.sync(now=NOW) == 0
    assert source.calls == []
    async with uow_factory() as uow:
        assert list(await uow.session.scalars(select(Match))) == []


async def test_source_failure_reported_to_admins_once_a_day(
    services: Services, notifier: FakeNotifier, source: FakeSource, squad_id: int
) -> None:
    source.error = "HTTP 502"
    await services.matches.sync(now=NOW)
    await services.matches.sync(now=NOW + timedelta(minutes=30))
    [report] = notifier.to(ADMIN_TG_ID)
    assert report.text == ru.MATCH_SOURCE_FAILED.format(
        squad="Взрослые", team_id=TEAM_ID, error="HTTP 502"
    )
    await services.matches.sync(now=NOW + timedelta(days=1))
    assert len(notifier.to(ADMIN_TG_ID)) == 2


async def test_search_window_after_played_game(
    services: Services, notifier: FakeNotifier, source: FakeSource, squad_id: int
) -> None:
    """Сыграли в субботу — до вторника 12:00 mtgame не опрашивается, со вторника — пока не
    найдётся следующая игра."""
    source.games = [SCHEDULED]
    await services.matches.sync(now=NOW)
    after_game = KICKOFF + timedelta(hours=3)  # сб 18.10
    too_early = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)  # вт 11:00 МСК — ещё рано
    source.games = [replace(DRAFT, external_id="mtgame:2", tour=8)]
    for moment in (after_game, too_early):
        await services.matches.sync(now=moment)
    assert source.calls == [TEAM_ID]

    tuesday = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)  # 12:00 МСК — поиск открыт
    await services.matches.sync(now=tuesday)
    await services.matches.sync(now=tuesday + timedelta(hours=5))  # даты пока нет — ищем
    assert len(source.calls) == 3
    next_game = replace(
        DRAFT, external_id="mtgame:2", tour=8, starts_at=KICKOFF + timedelta(days=7), venue=VENUE
    )
    source.games = [next_game]
    assert await services.matches.sync(now=tuesday + timedelta(hours=10)) == 1
    await services.matches.sync(now=tuesday + timedelta(hours=11))
    assert len(source.calls) == 4  # найдена — поиск закрыт
    assert len(notifier.polls) == 2


async def test_vanished_game_reported_to_coaches(
    services: Services,
    notifier: FakeNotifier,
    source: FakeSource,
    uow_factory: UowFactory,
    squad_id: int,
) -> None:
    await join(services, COACH, at=NOW - timedelta(days=30), role=SquadRole.COACH)
    await monitor(services, squad_id)
    source.games = [SCHEDULED]
    await services.matches.sync(now=NOW)
    source.games = [DRAFT]  # у игры сняли дату
    await services.matches.sync(now=NOW + timedelta(minutes=30))
    source.games = []  # и вовсе убрали из расписания
    await services.matches.sync(now=NOW + timedelta(hours=1))
    [warning] = notifier.to(COACH)
    assert warning.text.startswith("⚠️") and "PFBC — Hungry dogs OLD" in warning.text
    assert "18.10.2026 12:30" in warning.text
    assert len(await match_events(uow_factory)) == 1  # матч не удаляется — решает тренер


async def test_linked_by_division(
    services: Services,
    notifier: FakeNotifier,
    source: FakeSource,
    uow_factory: UowFactory,
    squad_id: int,
) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    season = await services.events.create(
        admin,
        EventCreate(
            squad_id=squad_id,
            type=EventType.TOURNAMENT,
            title="ABL 2026/27",
            is_season=True,
            division="москвич",
        ),
        now=NOW,
    )
    source.games = [SCHEDULED]
    await services.matches.sync(now=NOW)
    [event] = await match_events(uow_factory)
    assert event.tournament_id == season.id and event.description == "Тур 7"


async def test_tournament_title_and_division_edit(services: Services, squad_id: int) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    cup = await services.events.create(
        admin,
        EventCreate(squad_id=squad_id, type=EventType.TOURNAMENT, title="ABL", is_season=True),
        now=NOW,
    )
    assert cup.division is None
    renamed = await services.events.rename_tournament(admin, cup.id, "  ABL 2026/27 ")
    assert renamed.title == "ABL 2026/27"
    assert (await services.events.set_division(admin, cup.id, "Москвич")).division == "Москвич"
    assert (await services.events.set_division(admin, cup.id, None)).division is None
    with pytest.raises(Conflict):
        await services.events.rename_tournament(admin, cup.id, "   ")
    await join(services, PLAYER, at=NOW - timedelta(days=30))
    with pytest.raises(PermissionDenied):
        await services.events.set_division(await ctx_of(services, PLAYER), cup.id, "X")
    with pytest.raises(ValidationError):
        EventCreate(
            squad_id=squad_id,
            type=EventType.MATCH,
            title="Матч",
            starts_at=KICKOFF,
            division="Москвич",
        )
