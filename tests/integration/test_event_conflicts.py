"""ФТ-9: пересечение события с тренировками, матчами и другими событиями состава."""

from datetime import UTC, date, datetime, time

import pytest

from pfbc_bot.db.models import Match
from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import EventType, MatchSource, SquadRole
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.schemas.trainings import TrainingCreate
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import PermissionDenied
from pfbc_bot.services.events import ScheduleConflictError

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
    return await ctx_of(services, CAPTAIN)


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
