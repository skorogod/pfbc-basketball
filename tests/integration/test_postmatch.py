"""Полный цикл: участники, неизменяемые ответы, снимки и независимая доставка."""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy import func, select

from pfbc_bot.db.models import Event, MatchSurvey, ReportRecipient, SurveyPlayer, SurveyReport
from pfbc_bot.domain.dates import utcnow
from pfbc_bot.domain.enums import EventType, SquadRole, TopicPurpose
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict, PermissionDenied

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, ctx_of, join, setup_squad


async def test_survey_lifecycle(
    services: Services, uow_factory: UowFactory, notifier: FakeNotifier, tmp_path: Path
) -> None:
    squad = await setup_squad(services)
    admin = await ctx_of(services, ADMIN_TG_ID)
    await services.squads.bind_topic(admin, CHAT_ID, TopicPurpose.MATCH_ANALYSIS, 44)
    svc = services.postmatch
    svc.reports.directory = tmp_path
    await services.settings.set_squad_value(admin, squad.id, "features.post_match_surveys", "true")
    now = utcnow().replace(hour=11, minute=0)
    for tg_id in range(31, 37):
        await join(
            services,
            tg_id,
            at=now - timedelta(days=5),
            role=SquadRole.COACH if tg_id >= 35 else None,
        )
    first, second, third, fourth, coach, other = [await ctx_of(services, i) for i in range(31, 37)]
    async with uow_factory() as uow:
        for days in (2, 1):
            uow.session.add(
                Event(
                    type=EventType.MATCH,
                    title="Кубок",
                    opponent="Атлант",
                    squad_id=squad.id,
                    starts_at=now - timedelta(days=days),
                    duration_minutes=90,
                )
            )
        await uow.commit()
    await svc.tick(now=now)
    await svc.tick(now=now)
    [(sid, _)] = await svc.list_surveys(coach)
    # Пустая заявка: пропустили последний матч один раз, исторические игры не рассылаются.
    assert len([m for m in notifier.to(ADMIN_TG_ID) if "Нет участников" in m.text]) == 1
    for ctx in (first, second, third, fourth):
        await svc.change(coach, sid, ctx.user.id, True)
    async with uow_factory() as uow:
        tokens = {p.user_id: p.token for p in await uow.session.scalars(select(SurveyPlayer))}

    async def action(ctx, step, code, value=None):
        return await svc.player_action(ctx, tokens[ctx.user.id], step, code, value)

    with pytest.raises(Conflict):
        await svc.player_action(second, tokens[first.user.id], 0, "start")
    await action(first, 0, "start")
    with pytest.raises(Conflict, match="начал"):
        await svc.change(admin, sid, first.user.id, False)
    await svc.change(admin, sid, fourth.user.id, False)
    with pytest.raises(Conflict):
        await action(fourth, 0, "start")
    await svc.change(admin, sid, fourth.user.id, True)
    with pytest.raises(Conflict):
        await action(fourth, 0, "start")
    await svc.change(admin, sid, fourth.user.id, False)
    await action(third, 0, "yes_absent")
    with pytest.raises(Conflict):
        await svc.change(coach, sid, third.user.id, True)
    # Два тренера одновременно запросили один и тот же промежуточный снимок.
    await asyncio.gather(svc.reports.request(coach, sid), svc.reports.request(other, sid))
    async with uow_factory() as uow:
        [report] = list(await uow.session.scalars(select(SurveyReport)))
        assert report.snapshot["games"][0]["stats"]["n"] == 0
        assert await uow.session.scalar(select(func.count()).select_from(ReportRecipient)) == 2
    for step, code, value in [
        (1, "7", None),
        (2, "great", None),
        (3, "pain", None),
        (30, "text", "колено"),
        (4, "4", None),
        (5, "shot", None),
        (5, "done", None),
        (6, "skip", None),
        (7, "text", "частный комментарий"),
        (8, "submit", None),
    ]:
        await action(first, step, code, value)
    assert "уже принят" in (await action(first, 0, "start"))[0]
    await services.settings.set_squad_value(
        admin, squad.id, "survey_sensitive_users", f"[{coach.user.id}]"
    )
    await action(second, 0, "yes_declined")
    async with uow_factory() as uow:
        s = await uow.session.get(MatchSurvey, sid)
        assert s and (s.status, s.reason, s.responses, s.participants) == (
            "closed",
            "complete",
            1,
            1,
        )
        reports = list(await uow.session.scalars(select(SurveyReport)))
        final = [r for r in reports if r.snapshot["games"][0]["final"]]
        assert len(final) == 3
        for r in final:
            game = r.snapshot["games"][0]
            assert game["stats"]["absent"] == game["stats"]["declined"] == 1
            assert ("частный комментарий" in str(game)) == (r.visibility == "sensitive")
            if r.visibility == "group":
                assert not game["rows"]
    await svc.reports.process()
    assert notifier.to(CHAT_ID)[-1].thread_id == 44
    doc = Document(notifier.to(CHAT_ID)[-1].text)
    assert len(doc.inline_shapes) == 2
    assert not doc.tables
    assert "готов, отправляю" in await svc.reports.request(coach, sid)
    await svc.reports.process()
    assert len([m for m in notifier.to(CHAT_ID) if m.thread_id == 44]) == 1
    await svc.erase(admin, sid, first.user.id)
    async with uow_factory() as uow:
        assert all(
            "частный комментарий" not in str(r.snapshot)
            for r in await uow.session.scalars(select(SurveyReport))
        )


async def test_schedule_reminder_and_deadline(
    services: Services,
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from pfbc_bot.db.models import Poll, PollAnswer
    from pfbc_bot.domain.enums import MATCH_POLL_PLAYER, PollType
    from pfbc_bot.services.events import EVENT_REF

    admin = await ctx_of(services, ADMIN_TG_ID)
    squad = await setup_squad(services)
    await services.settings.set_squad_value(admin, squad.id, "features.post_match_surveys", "true")
    svc = services.postmatch
    svc.reports.directory = tmp_path
    # Матч заканчивается в 23:00 МСК; запланированная 01:00 переносится на 08:00.
    now = datetime(2026, 11, 21, 20, tzinfo=UTC)
    monkeypatch.setattr("pfbc_bot.services.postmatch.utcnow", lambda: now)
    await join(services, 41, at=now - timedelta(days=2))
    player = await ctx_of(services, 41)
    async with uow_factory() as uow:
        e = Event(
            type=EventType.MATCH,
            squad_id=squad.id,
            title="Москва",
            starts_at=now - timedelta(hours=1),
            duration_minutes=60,
        )
        uow.session.add(e)
        await uow.session.flush()
        poll = Poll(
            type=PollType.MATCH,
            squad_id=squad.id,
            ref_type=EVENT_REF,
            ref_id=e.id,
            tg_poll_id="test-postmatch",
            chat_id=CHAT_ID,
            message_id=56,
            question="Игра",
            options=[MATCH_POLL_PLAYER, "Нет"],
        )
        uow.session.add(poll)
        await uow.session.flush()
        uow.session.add(
            PollAnswer(poll_id=poll.id, user_id=player.user.id, option_ids=[0], answered_at=now)
        )
        await uow.commit()
    await asyncio.gather(svc.tick(now=now), svc.tick(now=now))
    [(sid, _)] = await svc.list_surveys(admin)
    async with uow_factory() as uow:
        s = await uow.session.get(MatchSurvey, sid)
        assert s and s.status == "scheduled" and s.scheduled_at == now + timedelta(hours=9)
    now += timedelta(hours=9)
    await svc.tick(now=now)
    async with uow_factory() as uow:
        p = (await uow.session.scalars(select(SurveyPlayer))).one()
        assert p.user_id == player.user.id
        token = p.token
    assert "Ответить" in notifier.to(41)[-1].buttons()
    await svc.tick(now=now)
    now += timedelta(hours=12)
    await svc.tick(now=now)
    await svc.tick(now=now)
    assert len([m for m in notifier.to(41) if "Напоминание." in m.text]) == 1
    now += timedelta(hours=12)
    with pytest.raises(Conflict):
        await svc.player_action(player, token, 0, "start")
    await svc.tick(now=now)
    async with uow_factory() as uow:
        s = await uow.session.get(MatchSurvey, sid)
        assert s and s.reason == "deadline" and (s.responses, s.participants) == (0, 1)
    await svc.reports.process()
    doc = Document(notifier.to(CHAT_ID)[-1].text)
    assert len(doc.inline_shapes) == 0
    assert any("Нет данных · Ответили 0 из 1" in p.text for p in doc.paragraphs)


async def test_history_periods_and_player_comparison(
    services: Services, uow_factory: UowFactory, notifier: FakeNotifier, tmp_path: Path
) -> None:
    squad = await setup_squad(services)
    admin = await ctx_of(services, ADMIN_TG_ID)
    now = utcnow()
    for uid in (61, 62):
        await join(services, uid, at=now - timedelta(days=10))
    players = [await ctx_of(services, uid) for uid in (61, 62)]
    svc = services.postmatch
    svc.reports.directory = tmp_path
    async with uow_factory() as uow:
        for day in range(1, 7):
            at = now - timedelta(days=7 - day, hours=4)
            e = Event(
                type=EventType.MATCH,
                squad_id=squad.id,
                title=f"M{day}",
                starts_at=at,
                duration_minutes=60,
            )
            uow.session.add(e)
            await uow.session.flush()
            s = MatchSurvey(event_id=e.id, status="closed", scheduled_at=at)
            uow.session.add(s)
            await uow.session.flush()
            for i, ctx in enumerate(players):
                p = svc._player(s.id, ctx.user.id)
                p.status = "absent" if (i == 1 and day < 5) else "submitted"
                p.notice = None
                if p.status == "submitted":
                    p.answers = {
                        "1": str(day),
                        "2": "great",
                        "3": "no",
                        "4": "4",
                        "5": ["shot"],
                        "6": ["help"],
                    }
                uow.session.add(p)
            sid = s.id
        await uow.commit()
    with pytest.raises(PermissionDenied, match="Нет доступа"):
        await svc.reports.request(players[0], sid, period="5")
    for period in ("5", "10", "20", "all", "2000-01-01..2000-01-02"):
        await svc.reports.request(admin, sid, period=period)
    await svc.reports.request(admin, sid, period="5", player=players[1].user.id)
    await svc.reports.request(admin, sid, kind="csv")
    async with uow_factory() as uow:
        reports = list(await uow.session.scalars(select(SurveyReport)))
        for r in reports:
            games = r.snapshot["games"]
            if r.kind == "csv":
                continue
            if r.snapshot["player"]:
                assert [g["opponent"] for g in games] == ["M5", "M6"]
            elif r.snapshot["period"] == "5":
                assert [g["opponent"] for g in games] == ["M2", "M3", "M4", "M5", "M6"]
            elif ".." in r.snapshot["period"]:
                assert not games
            else:
                assert len(games) == 6
    await svc.reports.process()
    files = [m.text for m in notifier.to(ADMIN_TG_ID)]
    assert sum(name.endswith(".docx") for name in files) == 6
    assert sum(name.endswith(".csv") for name in files) == 1
    assert len(notifier.to(ADMIN_TG_ID)) >= 7
