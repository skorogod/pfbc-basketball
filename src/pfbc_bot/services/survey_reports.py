"""Очередь сборки и доставки послематчевых отчётов."""

import asyncio
import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from pfbc_bot.db.models import (
    Event,
    Match,
    MatchSurvey,
    ReportRecipient,
    Squad,
    SurveyPlayer,
    SurveyReport,
    User,
)
from pfbc_bot.domain.dates import format_local, to_local, utcnow
from pfbc_bot.domain.enums import EventType, MembershipStatus, SquadRole, TopicPurpose
from pfbc_bot.domain.postmatch import player_labels
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.scheduler.locks import lock_key
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.common import resolve_destination, to_dto
from pfbc_bot.services.errors import Conflict, PermissionDenied
from pfbc_bot.services.ports import Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.services.survey_documents import render_report, summarize


async def survey_rows(uow: UnitOfWork, survey_id: int) -> list[tuple[SurveyPlayer, User]]:
    result = await uow.session.execute(
        select(SurveyPlayer, User)
        .join(User)
        .where(SurveyPlayer.survey_id == survey_id)
        .order_by(User.full_name, User.id)
    )
    return [(p, u) for p, u in result]


async def game_snapshot(
    uow: UnitOfWork, survey: MatchSurvey | None, event: Event, squad: Squad, visibility: str
) -> dict[str, Any]:
    source = await uow.session.get(Match, event.match_id) if event.match_id else None
    rows = []
    for p, user in await survey_rows(uow, survey.id) if survey else []:
        answers = p.answers if p.status == "submitted" else {}
        if visibility != "sensitive":
            answers = {k: v for k, v in answers.items() if k in {"1", "4", "5", "6"}}
        rows.append(
            {
                "id": p.user_id,
                "name": to_dto(user).display_name,
                "status": p.status,
                "answers": answers,
            }
        )
    assert event.starts_at is not None
    return {
        "event": event.id,
        "squad": squad.name,
        "date": to_local(event.starts_at, squad.timezone).strftime("%d.%m.%Y"),
        "opponent": event.opponent or (source.opponent if source else event.title),
        "final": bool(survey and survey.status == "closed"),
        "rows": rows if visibility != "group" else [],
        "stats": summarize(rows),
    }


class SurveyReports:
    def __init__(self, factory: UowFactory, notifier: Notifier, admins: frozenset[int]) -> None:
        self.factory = factory
        self.notifier = notifier
        self.admins = admins
        self.directory = Path(os.environ.get("SURVEY_REPORT_DIR", "reports"))

    def path(self, report: SurveyReport) -> Path:
        return self.directory / f"{report.key}.{report.kind}"

    async def _queue(
        self,
        uow: UnitOfWork,
        squad: Squad,
        games: list[dict[str, Any]],
        visibility: str,
        kind: str,
        chat: int,
        automatic: bool,
        *,
        player: int = 0,
        history: bool = False,
        player_name: str = "",
        period: str = "match",
    ) -> bool:
        snapshot = {
            "games": games,
            "visibility": visibility,
            "player": player,
            "history": history,
            "period": period,
            "player_name": player_name,
            "title": (
                "История матчей · " + (player_name or squad.name)
                if history
                else "Сводная таблица по матчу"
            ),
        }
        scope = [
            squad.id,
            kind,
            visibility,
            player,
            period,
            games[0]["event"] if not history else None,
        ]
        family = hashlib.sha256(json.dumps(scope).encode()).hexdigest()
        key = hashlib.sha256(
            json.dumps([squad.id, kind, snapshot], sort_keys=True).encode()
        ).hexdigest()
        now = utcnow()
        days = (await load_squad_settings(uow, squad.id)).survey_report_days
        await uow.session.execute(
            insert(SurveyReport)
            .values(
                family=family,
                key=key,
                squad_id=squad.id,
                kind=kind,
                visibility=visibility,
                snapshot=snapshot,
                created_at=now,
                expires_at=now + timedelta(days=days),
            )
            .on_conflict_do_nothing()
        )
        report = (
            await uow.session.scalars(
                select(SurveyReport).where(SurveyReport.key == key).with_for_update()
            )
        ).one()
        if (
            report.expires_at <= now
            or (report.ready and not self.path(report).is_file())
            or report.attempts >= 3
        ):
            report.ready = False
            report.attempts = 0
            report.expires_at = now + timedelta(days=days)
        stmt = insert(ReportRecipient).values(
            report_id=report.id, chat_id=chat, automatic=automatic, status="pending"
        )
        await uow.session.execute(
            stmt.on_conflict_do_update(
                index_elements=["report_id", "chat_id"],
                set_={"status": "pending", "attempts": 0, "automatic": automatic},
                where=ReportRecipient.status != "pending",
            )
        )
        return report.ready

    async def final(self, uow: UnitOfWork, survey: MatchSurvey, event: Event, squad: Squad) -> None:
        dest = await resolve_destination(uow, squad.id, TopicPurpose.MATCH_ANALYSIS)
        if dest:
            game = await game_snapshot(uow, survey, event, squad, "group")
            await self._queue(uow, squad, [game], "group", "docx", dest.chat_id, True)
        roles = await uow.members.roles_in_squad(squad.id)
        sensitive = (await load_squad_settings(uow, squad.id)).survey_sensitive_users
        versions = {}
        for _, user in await uow.members.list_active_for_squad(squad.id):
            if (
                user.is_admin
                or user.tg_user_id in self.admins
                or SquadRole.COACH in roles.get(user.id, set())
            ):
                visibility = "sensitive" if user.id in sensitive else "staff"
                if visibility not in versions:
                    versions[visibility] = await game_snapshot(
                        uow, survey, event, squad, visibility
                    )
                await self._queue(
                    uow, squad, [versions[visibility]], visibility, "docx", user.tg_user_id, True
                )

    async def request(
        self,
        ctx: UserContext,
        survey_id: int,
        *,
        period: str = "match",
        player: int = 0,
        kind: str = "docx",
    ) -> str:
        async with self.factory() as uow:
            survey = await uow.session.get(
                MatchSurvey, survey_id, with_for_update=period == "match"
            )
            event = await uow.session.get(Event, survey.event_id) if survey else None
            if not event or event.deleted_at or not ctx.has_role(event.squad_id, SquadRole.COACH):
                raise PermissionDenied("Нет доступа к отчёту.")
            if kind not in {"docx", "csv"} or (kind == "csv" and not ctx.is_admin):
                raise PermissionDenied("Нет доступа к этому формату.")
            squad = await uow.session.get(Squad, event.squad_id)
            assert squad and survey
            settings = await load_squad_settings(uow, squad.id)
            visibility = "sensitive" if ctx.user.id in settings.survey_sensitive_users else "staff"
            history = period != "match"
            player_name = ""
            if history:
                query = (
                    select(MatchSurvey, Event)
                    .select_from(Event)
                    .outerjoin(MatchSurvey, MatchSurvey.event_id == Event.id)
                    .where(
                        Event.squad_id == squad.id,
                        Event.deleted_at.is_(None),
                        Event.type == EventType.MATCH,
                        Event.starts_at + Event.duration_minutes * text("interval '1 minute'")
                        <= utcnow(),
                    )
                )
                if player:
                    query = query.join(
                        SurveyPlayer, SurveyPlayer.survey_id == MatchSurvey.id
                    ).where(
                        SurveyPlayer.user_id == player,
                        SurveyPlayer.status.not_in(["absent", "removed"]),
                    )
                    if await uow.members.get(player, squad.id) is None:
                        raise PermissionDenied("Игрок не состоит в выбранном составе.")
                    user = await uow.session.get(User, player)
                    assert user
                    names = {
                        u.id: to_dto(u).display_name
                        for _, u in await uow.members.list_active_for_squad(squad.id)
                    }
                    names[player] = to_dto(user).display_name
                    player_name = player_labels(names)[player]
                bounds = None
                if period not in ("5", "10", "20", "all"):
                    try:
                        start, end = period.split("..")
                        bounds = (date.fromisoformat(start), date.fromisoformat(end))
                        if bounds[0] > bounds[1]:
                            raise ValueError
                    except ValueError as exc:
                        raise Conflict("Введите период ГГГГ-ММ-ДД..ГГГГ-ММ-ДД.") from exc
                query = query.order_by(Event.starts_at.desc(), Event.id.desc())
                if period.isdigit():
                    query = query.limit(int(period))
                items = list((await uow.session.execute(query)).all())[::-1]
                # Одинаковый порядок блокировок с удалением данных. Снимок всех матчей
                # собирается после фиксации конкурирующих изменений участников.
                await uow.session.execute(
                    select(MatchSurvey)
                    .where(MatchSurvey.id.in_([s.id for s, _ in items if s]))
                    .order_by(MatchSurvey.id)
                    .with_for_update(read=True)
                    .execution_options(populate_existing=True)
                )
                games = []
                for s, e in items:
                    if (
                        bounds
                        and not bounds[0]
                        <= to_local(e.starts_at, squad.timezone).date()
                        <= bounds[1]
                    ):
                        continue
                    games.append(await game_snapshot(uow, s, e, squad, visibility))
            else:
                games = [await game_snapshot(uow, survey, event, squad, visibility)]
            ready = await self._queue(
                uow,
                squad,
                games,
                visibility,
                kind,
                ctx.user.tg_user_id,
                False,
                player=player,
                history=history,
                player_name=player_name,
                period=period,
            )
            await uow.commit()
            return (
                "Отчёт готов, отправляю."
                if ready
                else "Отчёт готовится. Пришлю его сюда, когда он будет готов."
            )

    async def process(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        async with self.factory() as uow:
            pending = (
                select(ReportRecipient.id)
                .where(
                    ReportRecipient.report_id == SurveyReport.id,
                    ReportRecipient.status == "pending",
                )
                .exists()
            )
            newer = aliased(SurveyReport)
            replaced = (
                select(newer.id)
                .where(
                    newer.family == SurveyReport.family,
                    newer.id > SurveyReport.id,
                    newer.ready.is_(True),
                )
                .exists()
            )
            ids = list(
                await uow.session.scalars(
                    select(SurveyReport.id)
                    .where(pending | replaced | (SurveyReport.expires_at <= utcnow()))
                    .order_by(SurveyReport.id)
                )
            )
        for rid in ids:
            await self._process_one(rid)

    async def _process_one(self, rid: int) -> None:
        async with self.factory() as uow:
            report = await uow.session.get(SurveyReport, rid, with_for_update=True)
            if not report:
                return
            path = self.path(report)
            pending = list(
                await uow.session.scalars(
                    select(ReportRecipient)
                    .where(ReportRecipient.report_id == rid, ReportRecipient.status == "pending")
                    .order_by(ReportRecipient.id)
                )
            )
            replaced = not pending and await uow.session.scalar(
                select(SurveyReport.id)
                .where(
                    SurveyReport.family == report.family,
                    SurveyReport.id > rid,
                    SurveyReport.ready.is_(True),
                )
                .limit(1)
            )
            if replaced or report.expires_at <= utcnow():
                path.unlink(missing_ok=True)
                await uow.session.delete(report)
                await uow.commit()
                return
            if not pending:
                return
            needs_build = not report.ready or not path.is_file()
        if needs_build and not await self._build(rid):
            return
        for item in pending:
            await self._deliver(rid, item.id)

    async def _build(self, rid: int) -> bool:
        # Блокировка только между сборщиками. Новые подписчики регистрируются в БД,
        # пока тяжёлая сборка идёт в отдельном потоке.
        async with self.factory() as guard:
            acquired = await guard.session.scalar(
                select(func.pg_try_advisory_xact_lock(lock_key(f"post_match_report:{rid}")))
            )
            if not acquired:
                return False
            report = await guard.session.get(SurveyReport, rid)
            if not report:
                return False
            path = self.path(report)
            if report.ready and path.is_file():
                return True
            squad = await guard.session.get(Squad, report.squad_id)
            assert squad
            try:
                data = await asyncio.to_thread(
                    render_report,
                    report.snapshot,
                    report.kind,
                    format_local(report.created_at, squad.timezone),
                )
                # За время сборки админ мог удалить исходные данные. Проверяем под
                # блокировкой перед записью на диск. Удалённый файл не восстанавливаем.
                current = await guard.session.scalar(
                    select(SurveyReport)
                    .where(SurveyReport.id == rid)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if not current:
                    return False
                temp = path.with_suffix(".tmp")
                temp.write_bytes(data)
                temp.replace(path)
                current.ready = True
            except (OSError, ValueError, RuntimeError, TypeError, KeyError):
                current = await guard.session.get(
                    SurveyReport, rid, with_for_update=True, populate_existing=True
                )
                if current:
                    current.attempts += 1
                    if current.attempts >= 3:
                        recipients = await guard.session.scalars(
                            select(ReportRecipient).where(
                                ReportRecipient.report_id == rid,
                                ReportRecipient.status == "pending",
                            )
                        )
                        for delivery in recipients:
                            delivery.status = "failed"
                            if current.visibility != "group":
                                await self.notifier.send_message(
                                    delivery.chat_id,
                                    "Не удалось собрать отчёт. Запросите его ещё раз.",
                                )
                await guard.commit()
                return False
            await guard.commit()
            return True

    async def _deliver(self, rid: int, did: int) -> None:
        async with self.factory() as uow:
            report = await uow.session.get(SurveyReport, rid, with_for_update=True)
            delivery = await uow.session.get(ReportRecipient, did)
            if not report or not delivery or delivery.status != "pending":
                return
            thread = None
            if report.visibility == "group":
                dest = await resolve_destination(uow, report.squad_id, TopicPurpose.MATCH_ANALYSIS)
                allowed = bool(dest and dest.chat_id == delivery.chat_id)
                thread = dest.thread_id if dest else None
            else:
                user = await uow.users.get_by_tg_id(delivery.chat_id)
                roles = await uow.members.roles_in_squad(report.squad_id)
                member = await uow.members.get(user.id, report.squad_id) if user else None
                active = bool(member and member.status == MembershipStatus.ACTIVE)
                admin = bool(user and (user.is_admin or user.tg_user_id in self.admins))
                allowed = bool(
                    user
                    and (
                        (admin and (active or not delivery.automatic))
                        or (active and SquadRole.COACH in roles.get(user.id, set()))
                    )
                )
                if report.kind == "csv":
                    allowed = allowed and admin
                if report.visibility == "sensitive":
                    settings = await load_squad_settings(uow, report.squad_id)
                    allowed = bool(allowed and user and user.id in settings.survey_sensitive_users)
            if not allowed:
                delivery.status = "revoked"
            else:
                sent = await self.notifier.send_document(
                    delivery.chat_id, str(self.path(report)), thread_id=thread
                )
                delivery.attempts += 1
                if sent:
                    delivery.status = "sent"
                elif delivery.attempts >= 3:
                    delivery.status = "failed"
            await uow.commit()
