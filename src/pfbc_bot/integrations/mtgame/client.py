"""Адаптер MatchSource для mtgame.ru — сайта, на котором лига ABL ведёт турниры.

Сайт — SPA: данные отдаёт JSON API. Нужные методы публичные, авторизация (куки) не нужна:
- /api/v1/team/<id>/tournaments/ — турниры команды (идущие — со статусом не closed);
- /api/v1/team/<id>/tournaments/<tid>/games/ — игры команды в турнире, включая черновики
  будущих туров без даты и места (общий /team/<id>/games/ их не отдаёт и требует входа).
"""

from datetime import datetime
from typing import Any

import aiohttp

from pfbc_bot.logging import get_logger
from pfbc_bot.schemas.matches import ExternalGame
from pfbc_bot.services.ports import MatchSourceError

log = get_logger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)
USER_AGENT = "PFBCTelegramBot/1.0 (+https://t.me/pfbc)"
CLOSED_TOURNAMENT = "closed"
# статусы игры на mtgame: open, in_progress, closed, archived, draft (будущий тур)
UPCOMING_GAME_STATUSES = frozenset({"open", "draft"})
EXTERNAL_PREFIX = "mtgame:"


def external_id(game_id: int) -> str:
    return f"{EXTERNAL_PREFIX}{game_id}"


def active_tournament_ids(payload: Any) -> list[int]:
    return [int(t["id"]) for t in payload if t.get("status") != CLOSED_TOURNAMENT]


def _team_name(team: dict[str, Any] | None, fallback: str | None) -> str:
    return (team or {}).get("name") or fallback or "?"


def parse_game(team_id: int, game: dict[str, Any]) -> ExternalGame | None:
    """Игра из ответа API; None — уже сыгранная (или отменённая)."""
    if game.get("status") not in UPCOMING_GAME_STATUSES:
        return None
    team, competitor = game.get("team"), game.get("competitor_team")
    home = _team_name(team, None)
    away = _team_name(competitor, game.get("competitor_team_name"))
    we_are_home = (team or {}).get("id") == team_id
    raw_when = game.get("datetime")
    court = game.get("tournament_court") or {}
    tournament = game.get("tournament") or {}
    return ExternalGame(
        external_id=external_id(int(game["id"])),
        home=home,
        away=away,
        opponent=away if we_are_home else home,
        tournament=tournament.get("name") or "",
        starts_at=datetime.fromisoformat(raw_when) if raw_when else None,
        venue=game.get("location") or court.get("address") or None,
        tour=game.get("tournament_tour"),
        stage=game.get("playoff_stage") or None,
        raw={
            "id": game["id"],
            "status": game.get("status"),
            "datetime": raw_when,
            "location": game.get("location"),
            "court": court.get("name"),
            "tournament_id": game.get("tournament_id"),
            "tournament": tournament.get("name"),
            "tour": game.get("tournament_tour"),
            "playoff_stage": game.get("playoff_stage"),
            "team": home,
            "competitor": away,
        },
    )


def parse_games(team_id: int, payload: Any) -> list[ExternalGame]:
    games = [parse_game(team_id, game) for game in payload]
    return [g for g in games if g is not None]


class MtgameSource:
    """Сессия HTTP создаётся при первом запросе — процесс bot её не открывает."""

    def __init__(self, base_url: str = "https://mtgame.ru") -> None:
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def upcoming_games(self, team_id: int) -> list[ExternalGame]:
        try:
            tournaments = await self._get(f"/api/v1/team/{team_id}/tournaments/")
            games: list[ExternalGame] = []
            for tournament_id in active_tournament_ids(tournaments):
                payload = await self._get(
                    f"/api/v1/team/{team_id}/tournaments/{tournament_id}/games/"
                )
                games += parse_games(team_id, payload)
            return games
        except MatchSourceError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise MatchSourceError(f"mtgame недоступен: {exc!r}") from exc
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise MatchSourceError(f"mtgame: неожиданный формат ответа: {exc!r}") from exc

    async def _get(self, path: str) -> Any:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        async with self._session.get(self._base_url + path) as response:
            if response.status != 200:
                raise MatchSourceError(f"mtgame: {path} → HTTP {response.status}")
            return await response.json(content_type=None)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
