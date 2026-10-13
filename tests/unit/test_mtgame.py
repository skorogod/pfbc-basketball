"""Разбор ответа API mtgame.ru и оформление матча из него (ФТ-4)."""

from datetime import UTC, datetime, time, timedelta

from pfbc_bot.domain.enums import EventType
from pfbc_bot.integrations.mtgame.client import active_tournament_ids, parse_games
from pfbc_bot.schemas.events import EventDTO
from pfbc_bot.schemas.matches import ExternalGame
from pfbc_bot.services.matches import find_tournament, match_description, search_opens_at

TEAM_ID = 9410


def game(**overrides: object) -> dict[str, object]:
    """Игра в формате /api/v1/team/<id>/tournaments/<tid>/games/ (только нужные поля)."""
    data: dict[str, object] = {
        "id": 163743,
        "team": {"id": TEAM_ID, "name": "PFBC"},
        "competitor_team": {"id": 9873, "name": "Hungry dogs OLD"},
        "competitor_team_name": None,
        "datetime": None,
        "location": None,
        "status": "draft",
        "tournament_id": 4148,
        "tournament_tour": 7,
        "playoff_stage": "",
        "tournament_court": None,
        "tournament": {"id": 4148, "name": "Москвич", "alias": "mos"},
    }
    return {**data, **overrides}


def test_active_tournaments_skip_closed() -> None:
    payload = [
        {"id": 4148, "status": "in_progress"},
        {"id": 3252, "status": "closed"},
        {"id": 5000, "status": "open"},
    ]
    assert active_tournament_ids(payload) == [4148, 5000]


def test_draft_without_date() -> None:
    [parsed] = parse_games(TEAM_ID, [game()])
    assert parsed.external_id == "mtgame:163743"
    assert parsed.starts_at is None and parsed.venue is None
    assert parsed.title == "PFBC — Hungry dogs OLD"
    assert parsed.opponent == "Hungry dogs OLD"
    assert (parsed.tournament, parsed.tour, parsed.stage) == ("Москвич", 7, None)
    assert parsed.raw["competitor"] == "Hungry dogs OLD"


def test_scheduled_away_game() -> None:
    raw = game(
        team={"id": 1, "name": "Skills Drills Community"},
        competitor_team={"id": TEAM_ID, "name": "PFBC"},
        datetime="2026-10-18T09:30:00.000000Z",
        location="Волгоградский пр-т., 46/15с13",
        status="open",
    )
    [parsed] = parse_games(TEAM_ID, [raw])
    assert parsed.starts_at == datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
    assert parsed.venue == "Волгоградский пр-т., 46/15с13"
    assert parsed.title == "Skills Drills Community — PFBC"
    assert parsed.opponent == "Skills Drills Community"


def test_court_address_and_competitor_name_fallbacks() -> None:
    raw = game(
        competitor_team=None,
        competitor_team_name="Новая команда",
        tournament_court={"address": "Дубнинская ул., 75", "name": "Зал А"},
        playoff_stage="1 / 2",
    )
    [parsed] = parse_games(TEAM_ID, [raw])
    assert parsed.opponent == "Новая команда"
    assert parsed.venue == "Дубнинская ул., 75"
    assert parsed.stage == "1 / 2"


def test_played_games_skipped() -> None:
    games = [game(id=1, status="closed"), game(id=2, status="in_progress"), game(id=3)]
    assert [g.external_id for g in parse_games(TEAM_ID, games)] == ["mtgame:3"]


def test_description() -> None:
    base = ExternalGame("mtgame:1", "PFBC", "X", "X", "Москвич", tour=7)
    assert match_description(base, linked_to_tournament=False) == "Москвич, тур 7"
    assert match_description(base, linked_to_tournament=True) == "Тур 7"
    playoff = ExternalGame("mtgame:1", "PFBC", "X", "X", "HOPE", tour=None, stage="Финал")
    assert match_description(playoff, linked_to_tournament=False) == "HOPE, плей-офф: Финал"
    bare = ExternalGame("mtgame:1", "PFBC", "X", "X", "")
    assert match_description(bare, linked_to_tournament=False) is None


def tournament(event_id: int, title: str, **kw: object) -> EventDTO:
    data: dict[str, object] = {
        "id": event_id,
        "squad_id": 1,
        "type": EventType.TOURNAMENT,
        "title": title,
        "starts_at": None,
        "duration_minutes": 90,
        "is_season": True,
        "place": None,
        "description": None,
        "notify_group": False,
        **kw,
    }
    return EventDTO.model_validate(data)


def test_find_tournament_by_name() -> None:
    now = datetime(2026, 10, 13, tzinfo=UTC)
    finished = tournament(
        1,
        "Москвич",
        is_season=False,
        duration_days=2,
        starts_at=now - timedelta(days=30),
    )
    current = tournament(2, "ABL Москвич 2026/27")
    other = tournament(3, "HOPE")
    assert find_tournament([finished, other, current], "Москвич", now) == 2
    assert find_tournament([other], "москвич", now) is None
    assert find_tournament([current], "", now) is None


def test_find_tournament_by_division_first() -> None:
    now = datetime(2026, 10, 13, tzinfo=UTC)
    by_title = tournament(1, "Кубок Москвич")
    by_division = tournament(2, "ABL 2026/27", division=" Москвич ")
    assert find_tournament([by_title, by_division], "москвич", now) == 2


MSK = "Europe/Moscow"
TUESDAY, NOON = 1, time(12, 0)


def test_search_opens_on_tuesday_after_game() -> None:
    saturday_game = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)
    assert search_opens_at(saturday_game, TUESDAY, NOON, MSK) == datetime(
        2026, 10, 20, 9, 0, tzinfo=UTC
    )
    # игра во вторник утром — поиск открывается в тот же день в 12:00
    tuesday_morning = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)
    assert search_opens_at(tuesday_morning, TUESDAY, NOON, MSK) == datetime(
        2026, 10, 20, 9, 0, tzinfo=UTC
    )
    # игра во вторник вечером — уже на следующей неделе
    tuesday_evening = datetime(2026, 10, 20, 16, 0, tzinfo=UTC)
    assert search_opens_at(tuesday_evening, TUESDAY, NOON, MSK) == datetime(
        2026, 10, 27, 9, 0, tzinfo=UTC
    )
