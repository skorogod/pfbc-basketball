"""Матчи ABL из внешнего источника (mtgame.ru, ФТ-4).

Задача worker-а опрашивает источник по составам, у которых указан mtgame_team_id. Игра с
известными датой и временем → запись в matches и матч в журнале с постом и опросом участия в
топике match. Игры без даты (черновик следующего тура) пропускаются до появления даты.

Когда опрашивать: лига назначает дату следующей игры обычно во вторник вечером, поэтому
поиск открывается в match_search_weekday / match_search_time после сыгранной игры и
закрывается, как только найдена следующая. С features.match_monitoring источник опрашивается
на каждом тике: лига меняет время или зал → перенос (сообщение и новый опрос) или сообщение о
новом месте; игра пропала из расписания или у неё сняли дату → предупреждение тренерам.

Фолбэк: к дедлайну (match_fallback_weekday / match_fallback_time, по умолчанию четверг 20:00)
в журнале нет матча на ближайшие выходные (не нашёлся на mtgame или сайт недоступен) —
тренерам запрос «введите матч вручную / игры нет». Ручная запись имеет приоритет: найдя ту
же игру на mtgame, синхронизация её не переносит, а только сообщает о расхождении.
"""

import hashlib
import html
from collections.abc import Iterable
from datetime import datetime, time, timedelta

from pfbc_bot import callbacks
from pfbc_bot.db.models import Match
from pfbc_bot.domain.dates import format_local, local_to_utc, to_local, utcnow
from pfbc_bot.domain.enums import EventType, MatchSource
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.logging import get_logger
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.events import EventDTO
from pfbc_bot.schemas.matches import ExternalGame
from pfbc_bot.schemas.settings import SquadSettings
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.common import notify_admins, squad_tz
from pfbc_bot.services.events import EventService
from pfbc_bot.services.permissions import Recipients, require
from pfbc_bot.services.ports import InlineButton, MatchSourceError, Notifier
from pfbc_bot.services.ports import MatchSource as MatchSourcePort
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)

# матч, заведённый тренером вручную в пределах стольких часов от времени из источника, —
# тот же матч: к нему привязывается запись источника, второй пост и опрос не публикуются
MANUAL_MATCH_WINDOW = timedelta(hours=3)
TOURNAMENTS_WINDOW = timedelta(days=365)
SOURCE_FAILURE_KIND = "match_source"
MATCH_GONE_KIND = "match_gone"
MATCH_FALLBACK_KIND = "match_fallback"
MATCH_DIFF_KIND = "match_diff"
# фолбэк: матч ищется с дедлайна по понедельник (игровые выходные); запрос, пропущенный больше
# чем на столько (worker был выключен), уже не отправляется
FALLBACK_WINDOW = timedelta(days=4)
FALLBACK_GRACE = timedelta(hours=12)


def source_failure_key(squad_id: int, day: str) -> str:
    """Сбой источника — админам не чаще раза в сутки по составу."""
    return f"match_source:{squad_id}:{day}"


def match_gone_key(match_id: int) -> str:
    """Игра пропала из источника — тренерам один раз на матч."""
    return f"match_gone:{match_id}"


def fallback_key(squad_id: int, deadline: datetime) -> str:
    """Запрос «матч не найден» — один раз на состав за неделю (дедлайн)."""
    return f"match_fallback:{squad_id}:{deadline:%Y-%m-%d}"


def match_diff_key(match_id: int, game: ExternalGame) -> str:
    """Расхождение ручной записи с mtgame — сообщение один раз на каждое новое значение."""
    assert game.starts_at is not None
    venue = hashlib.sha256((game.venue or "").encode()).hexdigest()[:12]
    return f"match_diff:{match_id}:{game.starts_at:%Y%m%d%H%M}:{venue}"


def last_deadline(now: datetime, weekday: int, at: time, tz: str) -> datetime:
    """Последний наступивший дедлайн фолбэка: weekday в at (время состава), не позже now."""
    local = to_local(now, tz)
    day = local.date() - timedelta(days=(local.weekday() - weekday) % 7)
    deadline = local_to_utc(day, at, tz)
    return deadline if deadline <= now else local_to_utc(day - timedelta(days=7), at, tz)


def search_opens_at(last_game: datetime, weekday: int, at: time, tz: str) -> datetime:
    """Когда открывается поиск следующей игры: первый weekday в at (время состава) после
    начала последней сыгранной игры."""
    local = to_local(last_game, tz)
    day = local.date() + timedelta(days=(weekday - local.weekday()) % 7)
    opens = local_to_utc(day, at, tz)
    return opens if opens > last_game else local_to_utc(day + timedelta(days=7), at, tz)


def match_description(game: ExternalGame, *, linked_to_tournament: bool) -> str | None:
    """«Москвич, тур 7» / «Москвич, плей-офф: 1 / 2»; у привязанного к турниру журнала
    название турнира и так показывается — остаётся «Тур 7»."""
    if game.stage:
        stage = ru.MATCH_STAGE.format(stage=game.stage)
    elif game.tour is not None:
        stage = ru.MATCH_TOUR.format(tour=game.tour)
    else:
        stage = ""
    parts = [p for p in (None if linked_to_tournament else game.tournament, stage) if p]
    text = ", ".join(parts)
    return text[:1].upper() + text[1:] if text else None


def find_tournament(tournaments: Iterable[EventDTO], name: str, now: datetime) -> int | None:
    """Турнир журнала для турнира источника (на mtgame это дивизион ABL, например «Москвич»):
    сначала — с таким дивизионом, затем — с совпадающим названием (одно содержит другое).
    Регистр не учитывается, закончившиеся турниры не рассматриваются."""
    needle = name.strip().casefold()
    if not needle:
        return None
    current = [t for t in tournaments if t.ends_at is None or t.ends_at > now]
    for tournament in current:
        if tournament.division and tournament.division.strip().casefold() == needle:
            return tournament.id
    for tournament in current:
        title = tournament.title.strip().casefold()
        if needle in title or title in needle:
            return tournament.id
    return None


class MatchService:
    def __init__(
        self,
        uow_factory: UowFactory,
        notifier: Notifier,
        events: EventService,
        source: MatchSourcePort | None,
        recipients: Recipients,
        admin_tg_ids: Iterable[int],
    ) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier
        self._events = events
        self._source = source
        self._recipients = recipients
        self._admin_tg_ids = frozenset(admin_tg_ids)

    async def sync(self, *, now: datetime | None = None) -> int:
        """Тик синхронизации. Возвращает число новых и изменённых матчей."""
        if self._source is None:
            return 0
        now = now or utcnow()
        async with self._uow_factory() as uow:
            targets: list[tuple[int, str, str, int]] = []
            for squad in await uow.squads.list_active():
                settings = await load_squad_settings(uow, squad.id)
                team_id = settings.mtgame_team_id
                if team_id is None or not settings.features.match_sync:
                    continue
                if await self._should_poll(uow, squad.id, settings, squad.timezone, now):
                    targets.append((squad.id, squad.name, squad.timezone, team_id))
        changed = 0
        for squad_id, squad_name, tz, team_id in targets:
            try:
                games = await self._source.upcoming_games(team_id)
            except MatchSourceError as exc:
                log.warning("match_source_failed", squad_id=squad_id, error=str(exc))
                await self._report_failure(squad_id, squad_name, tz, team_id, exc, now)
                continue
            for game in games:
                if game.starts_at is None or game.starts_at <= now:
                    continue  # дата ещё не назначена или игра уже началась
                try:
                    changed += await self._apply(squad_id, game, now)
                except Exception:
                    log.exception("match_sync_failed", squad_id=squad_id, game=game.external_id)
            scheduled = {g.external_id for g in games if g.starts_at is not None}
            await self._warn_missing(squad_id, tz, scheduled, now)
        if targets:
            log.info("match_sync_done", squads=len(targets), changed=changed)
        return changed

    @staticmethod
    async def _should_poll(
        uow: UnitOfWork, squad_id: int, settings: SquadSettings, tz: str, now: datetime
    ) -> bool:
        """Мониторинг — всегда; иначе — пока ищется следующая игра: она ещё не найдена и
        поиск уже открылся (или игр ещё не было)."""
        if settings.features.match_monitoring:
            return True
        if await uow.matches.has_upcoming(squad_id, now):
            return False
        last = await uow.matches.last_started(squad_id, now)
        if last is None:
            return True
        opens = search_opens_at(last, settings.match_search_weekday, settings.match_search_time, tz)
        return now >= opens

    async def _signal_manual_diff(self, squad_id: int, match_id: int, game: ExternalGame) -> None:
        """Матч в журнале заведён вручную, а на mtgame время или зал другие — тренерам
        сообщение (один раз на каждое новое значение); сам матч не меняется."""
        assert game.starts_at is not None
        async with self._uow_factory() as uow:
            event = await uow.events.get_by_match(match_id)
            if event is None or event.starts_at is None:
                return
            same_time = event.starts_at == game.starts_at
            same_place = game.venue is None or event.place == game.venue
            if same_time and same_place:
                return
            if not await uow.system.claim_reminder(match_diff_key(match_id, game), MATCH_DIFF_KIND):
                return
            await uow.commit()  # отметка — до рассылки
            tz = squad_tz(await uow.squads.get(squad_id))
            text = ru.MATCH_DIFF.format(
                title=html.escape(event.title),
                ours=format_local(event.starts_at, tz)
                + (f", {html.escape(event.place)}" if event.place else ""),
                theirs=format_local(game.starts_at, tz)
                + (f", {html.escape(game.venue)}" if game.venue else ""),
            )
            await self._recipients.notify(uow, self._notifier, squad_id, Permission.EVENTS, text)
            await uow.commit()  # dm_available
        log.info("match_manual_diff", squad_id=squad_id, match_id=match_id)

    # --- фолбэк: к дедлайну матча нет — запрос ручного ввода ---
    async def fallback_due(self, *, now: datetime | None = None) -> int:
        """Тик worker-а: составам с mtgame_team_id, у которых к дедлайну в журнале нет матча
        на ближайшие выходные, — запрос тренерам «введите матч вручную / игры нет». Один раз
        за неделю; отметка — до рассылки."""
        now = now or utcnow()
        sent = 0
        async with self._uow_factory() as uow:
            squads = await uow.squads.list_active()
        for squad in squads:
            async with self._uow_factory() as uow:
                settings = await load_squad_settings(uow, squad.id)
                if (
                    settings.mtgame_team_id is None
                    or not settings.features.match_sync
                    or not settings.features.match_fallback
                ):
                    continue
                deadline = last_deadline(
                    now,
                    settings.match_fallback_weekday,
                    settings.match_fallback_time,
                    squad.timezone,
                )
                if now - deadline > FALLBACK_GRACE:
                    continue
                if await uow.events.list_of_type(
                    EventType.MATCH, deadline, deadline + FALLBACK_WINDOW, [squad.id]
                ):
                    continue  # матч на выходные есть (с mtgame или введён вручную)
                if not await uow.system.claim_reminder(
                    fallback_key(squad.id, deadline), MATCH_FALLBACK_KIND
                ):
                    continue
                await uow.commit()
                keyboard = [
                    [
                        InlineButton(
                            ru.MATCH_FALLBACK_BTN_ADD,
                            callbacks.pack(callbacks.MATCH_FB, callbacks.MATCH_FB_ADD, squad.id),
                        )
                    ],
                    [
                        InlineButton(
                            ru.MATCH_FALLBACK_BTN_NONE,
                            callbacks.pack(callbacks.MATCH_FB, callbacks.MATCH_FB_NONE, squad.id),
                        )
                    ],
                ]
                text = ru.MATCH_FALLBACK.format(
                    squad=html.escape(squad.name), team_id=settings.mtgame_team_id
                )
                await self._recipients.notify(
                    uow, self._notifier, squad.id, Permission.EVENTS, text, keyboard=keyboard
                )
                await uow.commit()  # dm_available
                sent += 1
                log.info("match_fallback_sent", squad_id=squad.id)
        return sent

    async def confirm_no_game(self, actor: UserContext, squad_id: int) -> None:
        """«Игры нет» в запросе фолбэка — отметка в журнале аудита."""
        require(actor, squad_id, Permission.EVENTS)
        async with self._uow_factory() as uow:
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="match.no_game",
                entity="squad",
                entity_id=squad_id,
            )
            await uow.commit()

    async def _warn_missing(
        self, squad_id: int, tz: str, scheduled: set[str], now: datetime
    ) -> None:
        """Будущий матч исчез из расписания источника (или у игры сняли дату) — тренерам
        в личку: возможно, игру отменили. Сам матч не удаляется — решает тренер."""
        async with self._uow_factory() as uow:
            for match in await uow.matches.list_upcoming_from_source(squad_id, now):
                if match.external_id in scheduled:
                    continue
                event = await uow.events.get_by_match(match.id)
                if event is None or event.deleted_at is not None:
                    continue
                if not await uow.system.claim_reminder(match_gone_key(match.id), MATCH_GONE_KIND):
                    continue
                await uow.commit()  # отметка — до рассылки: сбой не приведёт к повтору
                text = ru.MATCH_GONE.format(
                    title=html.escape(event.title), when=format_local(match.starts_at, tz)
                )
                await self._recipients.notify(
                    uow, self._notifier, squad_id, Permission.EVENTS, text
                )
                await uow.commit()  # dm_available
                log.warning("match_gone", squad_id=squad_id, match_id=match.id)

    async def _apply(self, squad_id: int, game: ExternalGame, now: datetime) -> int:
        assert game.starts_at is not None
        async with self._uow_factory() as uow:
            match = await uow.matches.get_by_external(squad_id, game.external_id)
            if match is None:
                match = Match(
                    squad_id=squad_id, source=MatchSource.PARSER, external_id=game.external_id
                )
                self._fill(match, game)
                await uow.matches.add(match)
                manual = await uow.events.find_unlinked_match(
                    squad_id,
                    game.starts_at - MANUAL_MATCH_WINDOW,
                    game.starts_at + MANUAL_MATCH_WINDOW,
                )
                if manual is not None:
                    # ручная запись — главная: синхронизация её не переносит, только сообщает
                    match.manual_override = True
                    manual.match_id = match.id
                    await uow.commit()
                    log.info("match_linked_to_manual", event_id=manual.id, match_id=match.id)
                    await self._signal_manual_diff(squad_id, match.id, game)
                    return 1
                event = None
            else:
                event = await uow.events.get_by_match(match.id)
                # заведён вручную (и до появления manual_override) — главная запись
                entered_by_hand = event is not None and event.created_by_user_id is not None
                if match.manual_override or entered_by_hand:
                    self._fill(match, game)
                    await uow.commit()
                    await self._signal_manual_diff(squad_id, match.id, game)
                    return 0
                changed = (match.starts_at, match.venue) != (game.starts_at, game.venue)
                self._fill(match, game)
                if event is not None and (event.deleted_at is not None or not changed):
                    # матч удалили из журнала — не возвращаем; без изменений — нечего делать
                    await uow.commit()
                    return 0
            tournament_id = await self._tournament_for(uow, squad_id, game.tournament, now)
            match_id, event_id = match.id, event.id if event is not None else None
            await uow.commit()
        if event_id is not None:
            updated = await self._events.update_from_match(
                event_id, game.starts_at, game.venue, now=now
            )
            return int(updated is not None)
        # новый матч (или запись есть, а событие не создалось из-за сбоя прошлого тика)
        await self._events.create_from_match(
            squad_id,
            match_id,
            game.title,
            game.starts_at,
            game.venue,
            description=match_description(game, linked_to_tournament=tournament_id is not None),
            tournament_id=tournament_id,
        )
        log.info("match_created", squad_id=squad_id, match_id=match_id, game=game.external_id)
        return 1

    @staticmethod
    def _fill(match: Match, game: ExternalGame) -> None:
        assert game.starts_at is not None
        match.opponent = game.opponent[:200]
        match.starts_at = game.starts_at
        match.venue = game.venue[:255] if game.venue else None
        if game.stage:
            match.stage = game.stage[:50]
        else:
            match.stage = ru.MATCH_TOUR.format(tour=game.tour) if game.tour is not None else None
        match.division = game.tournament[:100] or None
        match.raw_payload = game.raw

    @staticmethod
    async def _tournament_for(
        uow: UnitOfWork, squad_id: int, name: str, now: datetime
    ) -> int | None:
        tournaments = await uow.events.list_of_type(
            EventType.TOURNAMENT, now - TOURNAMENTS_WINDOW, now + TOURNAMENTS_WINDOW, [squad_id]
        )
        return find_tournament((EventDTO.model_validate(t) for t in tournaments), name, now)

    async def _report_failure(
        self,
        squad_id: int,
        squad_name: str,
        tz: str,
        team_id: int,
        error: MatchSourceError,
        now: datetime,
    ) -> None:
        day = to_local(now, tz).date().isoformat()
        async with self._uow_factory() as uow:
            if not await uow.system.claim_reminder(
                source_failure_key(squad_id, day), SOURCE_FAILURE_KIND
            ):
                return
            await uow.commit()
            text = ru.MATCH_SOURCE_FAILED.format(
                squad=html.escape(squad_name), team_id=team_id, error=html.escape(str(error))
            )
            await notify_admins(uow, self._notifier, self._admin_tg_ids, text)
