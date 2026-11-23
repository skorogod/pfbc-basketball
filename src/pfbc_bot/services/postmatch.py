"""Жизненный цикл послематчевой анкеты. Изменения сериализуются по строке опроса."""

from datetime import datetime, timedelta
from html import escape
from secrets import token_urlsafe

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert

from pfbc_bot.db.models import AuditLog, Event, MatchSurvey, Squad, SurveyPlayer, SurveyReport, User
from pfbc_bot.domain.dates import format_local, outside_quiet_hours, utcnow
from pfbc_bot.domain.enums import EventType, MembershipStatus, SquadRole
from pfbc_bot.domain.postmatch import (
    EXCLUDED,
    PLAYING_TIME,
    PLAYING_TIME_KEY,
    PLAYING_TIME_OPTIONS,
    REVIEW,
    STATUS,
    WAITING,
    advance,
    form,
    invitation,
)
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.settings import SquadSettings
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.common import notify_admins, to_dto
from pfbc_bot.services.errors import Conflict, PermissionDenied
from pfbc_bot.services.game_payments import match_voter_ids
from pfbc_bot.services.ports import InlineButton, Keyboard, Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.services.survey_reports import SurveyReports, survey_rows


class PostMatchService:
    def __init__(self, factory: UowFactory, notifier: Notifier, admins: frozenset[int]) -> None:
        self.factory = factory
        self.notifier = notifier
        self.admins = admins
        self.reports = SurveyReports(factory, notifier, admins)
        self.last_scan: datetime | None = None

    async def tick(self, *, now: datetime | None = None) -> None:
        now = now or utcnow()
        async with self.factory() as uow:
            for squad in await uow.squads.list_active():
                settings = await load_squad_settings(uow, squad.id)
                if not settings.features.post_match_surveys:
                    continue
                end = Event.starts_at + Event.duration_minutes * text("interval '1 minute'")
                query = (
                    select(Event.id)
                    .where(
                        Event.type == EventType.MATCH,
                        Event.deleted_at.is_(None),
                        Event.squad_id == squad.id,
                        end <= now,
                        end >= now - timedelta(days=7),
                    )
                    .order_by(end.desc(), Event.id.desc())
                )
                # Последняя игра подхватывается и при включении настройки на работающем worker.
                latest = await uow.session.scalar(query.limit(1))
                ids = {latest} if latest else set()
                if self.last_scan:
                    ids.update(await uow.session.scalars(query.where(end >= self.last_scan)))
                for eid in ids:
                    await uow.session.execute(
                        insert(MatchSurvey)
                        .values(event_id=eid, status="scheduled", scheduled_at=now)
                        .on_conflict_do_nothing(index_elements=["event_id"])
                    )
            await uow.commit()
            ids = set(
                await uow.session.scalars(
                    select(MatchSurvey.id).where(MatchSurvey.status.in_(["scheduled", "open"]))
                )
            )
        self.last_scan = now
        for sid in sorted(ids):
            await self._tick_survey(sid, now)
        await self.flush_notices(now=now)

    async def _load(self, uow: UnitOfWork, sid: int) -> tuple[MatchSurvey, Event, Squad]:
        survey = await uow.session.get(MatchSurvey, sid, with_for_update=True)
        if not survey:
            raise Conflict("Опрос не найден.")
        event = await uow.session.get(Event, survey.event_id)
        assert event
        squad = await uow.session.get(Squad, event.squad_id)
        assert squad
        return survey, event, squad

    async def _tick_survey(self, sid: int, now: datetime) -> None:
        async with self.factory() as uow:
            s, event, squad = await self._load(uow, sid)
            settings = await load_squad_settings(uow, squad.id)
            if event.deleted_at or not squad.is_active:
                s.status = "cancelled"
            elif not settings.features.post_match_surveys:
                return
            elif s.status == "scheduled":
                assert event.starts_at
                end = event.starts_at + timedelta(minutes=event.duration_minutes)
                s.scheduled_at = outside_quiet_hours(
                    end + timedelta(hours=settings.survey_delay_hours), squad.timezone
                )
                if now >= s.scheduled_at and now == outside_quiet_hours(now, squad.timezone):
                    ids = await self._roster_ids(uow, event.id)
                    overrides = {p.user_id: p for p, _ in await survey_rows(uow, s.id)}
                    active = {u.id for _, u in await uow.members.list_active_for_squad(squad.id)}
                    for uid in (ids & active) - overrides.keys():
                        uow.session.add(self._player(s.id, uid))
                    for uid, p in overrides.items():
                        if uid not in active:
                            p.status = "removed"
                        p.notice = "invite" if p.status == "invited" else None
                    await self._changed(uow, s, event, squad)
                    if s.participants:
                        self._open(s, settings, squad, now)
                    else:
                        s.status = "skipped"
                        await notify_admins(
                            uow,
                            self.notifier,
                            self.admins,
                            f"Нет участников для опроса по матчу «{escape(event.title)}». "
                            "Открыть вручную: /postmatch",
                        )
            elif s.status == "open":
                assert s.deadline and s.reminder_at
                if now >= s.deadline:
                    await self._finish(uow, s, event, squad, now, "deadline")
                elif now >= s.reminder_at and now == outside_quiet_hours(now, squad.timezone):
                    for p, _ in await survey_rows(uow, s.id):
                        if p.status in WAITING and not p.reminded and p.notice is None:
                            p.reminded = True
                            p.notice = "reminder"
                            p.attempts = 0
            await uow.commit()

    @staticmethod
    async def _roster_ids(uow: UnitOfWork, eid: int) -> set[int]:
        event = await uow.events.get(eid)
        if event and event.roster_player_ids is not None:
            return set(event.roster_player_ids)
        payment = await uow.game_payments.active_for_event(eid)
        roster = await uow.game_payments.player_ids(payment.id) if payment else None
        return set(await match_voter_ids(uow, eid)) if roster is None else roster

    @staticmethod
    def _player(sid: int, uid: int) -> SurveyPlayer:
        return SurveyPlayer(
            survey_id=sid,
            user_id=uid,
            token=token_urlsafe(16),
            status="invited",
            notice="invite",
            answers={},
            step=0,
        )

    @staticmethod
    def _open(s: MatchSurvey, settings: SquadSettings, squad: Squad, now: datetime) -> None:
        s.status = "open"
        s.opened_at = now
        s.deadline = now + timedelta(hours=settings.survey_deadline_hours)
        s.reminder_at = outside_quiet_hours(
            now + timedelta(hours=settings.survey_reminder_hours), squad.timezone
        )

    async def _finish(
        self,
        uow: UnitOfWork,
        s: MatchSurvey,
        event: Event,
        squad: Squad,
        now: datetime,
        reason: str,
    ) -> None:
        s.status = "closed"
        s.closed_at = now
        s.reason = reason
        await uow.session.flush()
        await self.reports.final(uow, s, event, squad)

    async def _changed(self, uow: UnitOfWork, s: MatchSurvey, event: Event, squad: Squad) -> None:
        rows = await survey_rows(uow, s.id)
        s.participants = sum(p.status not in EXCLUDED for p, _ in rows)
        s.responses = sum(p.status == "submitted" for p, _ in rows)
        if s.status == "open" and s.participants == s.responses:
            await self._finish(uow, s, event, squad, utcnow(), "complete")

    @staticmethod
    def _check(s: MatchSurvey, event: Event) -> None:
        if event.deleted_at or s.status != "open" or not s.deadline or s.deadline <= utcnow():
            raise Conflict("Опрос завершён или недоступен. Изменения закрыты.")

    async def flush_notices(self, pid: int | None = None, *, now: datetime | None = None) -> None:
        now = now or utcnow()
        async with self.factory() as uow:
            query = select(SurveyPlayer.id, SurveyPlayer.survey_id).where(
                SurveyPlayer.notice.is_not(None), SurveyPlayer.attempts < 3
            )
            if pid:
                query = query.where(SurveyPlayer.id == pid)
            ids = list((await uow.session.execute(query)).all())
        for player_id, sid in ids:
            async with self.factory() as uow:
                s, event, squad = await self._load(uow, sid)
                p = await uow.session.get(SurveyPlayer, player_id)
                if not p or not p.notice or p.attempts >= 3:
                    continue
                user = await uow.session.get(User, p.user_id)
                assert user
                kb = None
                if p.notice == "removed":
                    msg = (
                        f"Вас убрали из опроса по матчу «{escape(event.title)}». "
                        "Анкета больше недоступна."
                    )
                elif s.status != "open" or not s.deadline or s.deadline <= now or event.deleted_at:
                    p.notice = None
                    await uow.commit()
                    continue
                else:
                    if (
                        p.notice in ("invite", "reminder")
                        and outside_quiet_hours(now, squad.timezone) > now
                    ):
                        continue
                    member = await uow.members.get(p.user_id, squad.id)
                    if not member or member.status != MembershipStatus.ACTIVE:
                        p.notice = None
                        await uow.commit()
                        continue
                    assert event.starts_at
                    date = format_local(event.starts_at, squad.timezone)
                    deadline = format_local(s.deadline, squad.timezone)
                    msg = (
                        f"🏀 <b>Послематчевый опрос</b>\n\n"
                        f"📅 {date} · <b>{escape(event.title)}</b>\n"
                        f"⏰ <b>Ответьте до {deadline}</b>\n\n"
                        "💬 <b>Как прошла игра?</b>\n\n"
                        "👥 <b>Кто увидит ответы</b>\n"
                        "Команда увидит сводку, штаб — поимённые результаты. "
                        "Самочувствие, боль и комментарий увидят только получившие допуск.\n\n"
                        "ℹ️ <b>После отправки</b>\n"
                        "Отправленный ответ изменить нельзя. Для удаления данных обратитесь "
                        "к админу. Скачанные копии остаются у получателей."
                    )
                    kb = (
                        invitation(p.token)
                        if p.status == "invited"
                        else [[InlineButton("Продолжить", f"ps:{p.token}:0:start")]]
                    )
                    if p.notice == "reminder":
                        msg = "🔔 <b>Напоминание.</b>\n\n" + msg
                sent = await self.notifier.send_message(user.tg_user_id, msg, keyboard=kb)
                p.attempts += 1
                if sent:
                    p.notice = None
                await uow.commit()

    async def player_action(
        self, ctx: UserContext, token: str, step: int, action: str, value: str | None = None
    ) -> tuple[str, Keyboard | None, int]:
        async with self.factory() as uow:
            sid = await uow.session.scalar(
                select(SurveyPlayer.survey_id).where(
                    SurveyPlayer.token == token, SurveyPlayer.user_id == ctx.user.id
                )
            )
            if not sid:
                raise Conflict("Анкета недоступна.")
            s, event, squad = await self._load(uow, sid)
            p = await uow.session.scalar(
                select(SurveyPlayer).where(
                    SurveyPlayer.token == token, SurveyPlayer.user_id == ctx.user.id
                )
            )
            if not p or squad.id not in ctx.squad_ids or p.status == "removed":
                raise Conflict("Анкета недоступна.")
            if p.status == "submitted":
                return "Ваш ответ уже принят.", None, 0
            self._check(s, event)
            if p.status == "invited" and step == 0:
                if action == "start":
                    p.status = "started"
                    p.step = PLAYING_TIME
                    p.notice = None
                elif action in ("absent", "declined"):
                    return (
                        f"❓ <b>Подтвердить «{STATUS[action]}»?</b>\n\nРешение окончательное.",
                        [
                            [InlineButton("Подтвердить", f"ps:{token}:0:yes_{action}")],
                            [InlineButton("Назад", f"ps:{token}:0:invite")],
                        ],
                        0,
                    )
                elif action == "invite":
                    return "Выберите действие.", invitation(token), 0
                elif action in ("yes_absent", "yes_declined"):
                    p.status = action.removeprefix("yes_")
                    p.notice = None
                    await self._changed(uow, s, event, squad)
                    await uow.commit()
                    return "✅ <b>Решение сохранено.</b>\n\n" + STATUS[p.status] + ".", None, 0
                else:
                    raise Conflict("Анкета ещё не начата.")
            elif p.status != "started":
                raise Conflict("Решение уже принято.")
            elif step == 0 and action == "start":
                pass  # повторное открытие восстанавливает текущий шаг
            elif step != p.step:
                raise Conflict("Этот шаг уже завершён. Откройте анкету из приглашения.")
            elif step == REVIEW and action == "submit":
                if not all(str(q) in p.answers for q in (1, 2, 3, 4)):
                    raise Conflict("Заполните обязательные вопросы.")
                if p.answers.get(PLAYING_TIME_KEY) not in PLAYING_TIME_OPTIONS:
                    p.step = PLAYING_TIME
                    msg, kb = form(token, p.step, p.answers)
                    await uow.commit()
                    return msg, kb, p.step
                p.status = "submitted"
                p.submitted_at = utcnow()
                p.notice = None
                await self._changed(uow, s, event, squad)
                await uow.commit()
                return (
                    "✅ <b>Ответ принят!</b>\n\nСпасибо за обратную связь. "
                    "Ответы сохранены, изменить их нельзя.",
                    None,
                    0,
                )
            else:
                answers = dict(p.answers)
                p.step = advance(step, answers, action, value)
                p.answers = answers
            msg, kb = form(token, p.step, p.answers)
            await uow.commit()
            return msg, kb, p.step

    async def list_surveys(self, ctx: UserContext) -> list[tuple[int, str]]:
        async with self.factory() as uow:
            query = (
                select(MatchSurvey.id, Event.title, Event.starts_at, Squad.timezone)
                .join(Event, Event.id == MatchSurvey.event_id)
                .join(Squad, Squad.id == Event.squad_id)
                .where(Event.deleted_at.is_(None))
                .order_by(Event.starts_at.desc())
            )
            if not ctx.is_admin:
                query = query.where(Event.squad_id.in_(ctx.squads_with_roles(SquadRole.COACH)))
            return [
                (sid, f"{format_local(at, tz)} · {title}")
                for sid, title, at, tz in await uow.session.execute(query)
            ]

    async def card(self, ctx: UserContext, sid: int) -> tuple[str, list[tuple[int, str, str]]]:
        async with self.factory() as uow:
            s, event, squad = await self._load(uow, sid)
            if not ctx.has_role(squad.id, SquadRole.COACH) or event.deleted_at:
                raise PermissionDenied("Нет доступа к матчу.")
            rows = await survey_rows(uow, s.id)
            roster = await self._roster_ids(uow, event.id) if s.status == "scheduled" else set()
            players = {p.user_id: (to_dto(user).display_name, p.status) for p, user in rows}
            labels = {
                "scheduled": "Запланирован",
                "open": "Открыт",
                "closed": "Завершён",
                "skipped": "Пропущен",
                "cancelled": "Отменён",
            }
            lines = [
                f"🏀 <b>{escape(event.title)}</b>",
                "",
                f"📋 <b>Статус:</b> {labels[s.status]}",
                f"👥 <b>Ответили {s.responses} из {s.participants}</b>",
                "",
                "<b>Участники опроса</b>" if players else "Участников пока нет.",
            ]
            lines.extend(
                f"• <b>{escape(name)}</b> — {STATUS[status]}" for name, status in players.values()
            )
            for _, user in await uow.members.list_active_for_squad(squad.id):
                players.setdefault(
                    user.id, (to_dto(user).display_name, "invited" if user.id in roster else "new")
                )
            return "\n".join(lines), [
                (uid, name, status) for uid, (name, status) in players.items()
            ]

    async def change(self, ctx: UserContext, sid: int, uid: int, add: bool) -> None:
        async with self.factory() as uow:
            s, event, squad = await self._load(uow, sid)
            if not ctx.has_role(squad.id, SquadRole.COACH):
                raise PermissionDenied("Управлять опросом могут тренер и админ.")
            if add and s.status == "skipped" and not event.deleted_at:
                self._open(s, await load_squad_settings(uow, squad.id), squad, utcnow())
            scheduled = s.status == "scheduled" and event.deleted_at is None
            if not scheduled:
                self._check(s, event)
            p = await uow.session.scalar(
                select(SurveyPlayer).where(
                    SurveyPlayer.survey_id == sid, SurveyPlayer.user_id == uid
                )
            )
            if scheduled and not p and not add and uid in await self._roster_ids(uow, event.id):
                p = self._player(sid, uid)
                uow.session.add(p)
            if add:
                member = await uow.members.get(uid, squad.id)
                if not member or member.status != MembershipStatus.ACTIVE:
                    raise Conflict("Выберите действующего участника состава.")
                if p and p.status != "removed":
                    raise Conflict("Игрок уже в списке: " + STATUS[p.status])
                if not p:
                    p = self._player(sid, uid)
                    uow.session.add(p)
                else:
                    p.token = token_urlsafe(16)
                    p.status = "invited"
                    p.notice = "invite"
                    p.reminded = False
            else:
                if not p or p.status != "invited":
                    if p and p.status == "started":
                        raise Conflict("Игрок уже начал отвечать. Убрать его из опроса нельзя.")
                    raise Conflict(
                        "Убрать можно только приглашённого игрока, который ещё не начал отвечать."
                    )
                p.status = "removed"
                p.notice = "removed"
            if scheduled:
                p.notice = None
            elif add:
                p.notice = "manual"
            p.attempts = 0
            await uow.system.audit(
                actor_user_id=ctx.user.id,
                action="survey.add" if add else "survey.remove",
                entity="survey_player",
                entity_id=uid,
                payload={"survey": sid},
            )
            await self._changed(uow, s, event, squad)
            await uow.commit()
            pid = p.id
        if not scheduled:
            await self.flush_notices(pid)

    async def resend(self, ctx: UserContext, sid: int, uid: int) -> None:
        if not ctx.is_admin:
            raise PermissionDenied("Повторную отправку выполняет админ.")
        async with self.factory() as uow:
            s, event, _ = await self._load(uow, sid)
            self._check(s, event)
            p = await uow.session.scalar(
                select(SurveyPlayer).where(
                    SurveyPlayer.survey_id == sid, SurveyPlayer.user_id == uid
                )
            )
            if not p or p.status not in WAITING:
                raise Conflict("Участник уже принял решение.")
            p.notice = "manual"
            p.attempts = 0
            await uow.commit()
        await self.flush_notices(p.id)

    async def erase(self, ctx: UserContext, sid: int, uid: int) -> None:
        """По запросу игрока админ удаляет анкеты и связанные снимки в данном составе."""
        if not ctx.is_admin:
            raise PermissionDenied("Удаление данных доступно админу.")
        async with self.factory() as uow:
            anchor = await uow.session.get(MatchSurvey, sid)
            event = await uow.session.get(Event, anchor.event_id) if anchor else None
            if not event:
                raise Conflict("Матч не найден.")
            squad = await uow.session.get(Squad, event.squad_id)
            assert squad
            items = list(
                (
                    await uow.session.execute(
                        select(MatchSurvey, Event)
                        .join(Event)
                        .where(Event.squad_id == squad.id)
                        .order_by(MatchSurvey.id)
                        .with_for_update(of=MatchSurvey)
                    )
                ).all()
            )
            affected = []
            for s, e in items:
                p = await uow.session.scalar(
                    select(SurveyPlayer).where(
                        SurveyPlayer.survey_id == s.id, SurveyPlayer.user_id == uid
                    )
                )
                if p:
                    await uow.session.delete(p)
                    affected.append((s, e))
            if not affected:
                raise Conflict("Данных игрока в этом составе нет.")
            # Групповые снимки обезличены: инвалидируем по событиям.
            events = {e.id for _, e in affected}
            reports = list(
                await uow.session.scalars(
                    select(SurveyReport).where(SurveyReport.squad_id == squad.id).with_for_update()
                )
            )
            for report in reports:
                if any(g["event"] in events for g in report.snapshot["games"]):
                    self.reports.path(report).unlink(missing_ok=True)
                    await uow.session.delete(report)
            await uow.session.execute(
                delete(AuditLog).where(
                    AuditLog.entity == "survey_player",
                    AuditLog.entity_id == str(uid),
                    AuditLog.payload["survey"].as_integer().in_([s.id for s, _ in affected]),
                )
            )
            await uow.session.flush()
            for s, e in affected:
                was_closed = s.status == "closed"
                await self._changed(uow, s, e, squad)
                if was_closed:
                    await self.reports.final(uow, s, e, squad)
            await uow.commit()
