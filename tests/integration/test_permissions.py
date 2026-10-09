"""Права ролей: значения по умолчанию, закреплённые права, изменение админом, адресаты."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import EventType, SquadRole
from pfbc_bot.domain.permissions import DEFAULT_ROLE_PERMISSIONS, Permission
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict, PermissionDenied
from pfbc_bot.services.permissions import PERMISSIONS_SCOPE, invalidate_permissions_cache

from .conftest import ADMIN_TG_ID
from .helpers import ctx_of, join, setup_squad

LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
COACH, CAPTAIN, PLAYER = 100, 101, 1


def at(day_offset: int, hour: int) -> datetime:
    return local_to_utc(NOW.date() + timedelta(days=day_offset), time(hour), "Europe/Moscow")


@pytest.fixture
async def squad_id(services: Services) -> int:
    squad = await setup_squad(services)
    await join(services, COACH, at=LONG_AGO, role=SquadRole.COACH)
    await join(services, CAPTAIN, at=LONG_AGO, role=SquadRole.CAPTAIN)
    await join(services, PLAYER, at=LONG_AGO)
    return squad.id


async def test_defaults(services: Services, squad_id: int) -> None:
    coach = await ctx_of(services, COACH)
    captain = await ctx_of(services, CAPTAIN)
    player = await ctx_of(services, PLAYER)
    admin = await ctx_of(services, ADMIN_TG_ID)
    assert coach.can(squad_id, Permission.SCHEDULE) and not coach.can(squad_id, Permission.DUTY)
    assert coach.can(squad_id, Permission.EVENTS) and coach.can(squad_id, Permission.GAME_PAYMENTS)
    assert captain.can(squad_id, Permission.DUTY) and captain.can(squad_id, Permission.MEETINGS)
    for perm in (Permission.EVENTS, Permission.GAME_PAYMENTS, Permission.SCHEDULE):
        assert not captain.can(squad_id, perm)
    assert player.can(squad_id, Permission.ATTENDANCE)
    assert not player.can(squad_id, Permission.MEETINGS)
    assert all(admin.can(squad_id, p) for p in Permission)


async def test_captain_only_meetings(services: Services, squad_id: int) -> None:
    """Капитан: собрание — можно; игры, турниры, разборы, стоимость, сбор денег — нет."""
    captain = await ctx_of(services, CAPTAIN)
    meeting = await services.events.create(
        captain,
        EventCreate(
            squad_id=squad_id, type=EventType.MEETING, title="Собрание", starts_at=at(3, 19)
        ),
        now=NOW,
    )
    await services.events.delete(captain, meeting.id, now=NOW)
    for event_type, extra in (
        (EventType.MATCH, {}),
        (EventType.REVIEW, {}),
        (EventType.CAMP, {}),
        (EventType.TOURNAMENT, {"duration_days": 2}),
    ):
        with pytest.raises(PermissionDenied):
            await services.events.create(
                captain,
                EventCreate(
                    squad_id=squad_id,
                    type=event_type,
                    title="x",
                    starts_at=at(4, 12),
                    **extra,
                ),
                now=NOW,
            )
    coach = await ctx_of(services, COACH)
    cup = await services.events.create(
        coach,
        EventCreate(squad_id=squad_id, type=EventType.TOURNAMENT, title="Кубок", is_season=True),
        now=NOW,
    )
    with pytest.raises(PermissionDenied):
        await services.events.set_game_price(captain, cup.id, Decimal("1000"))
    with pytest.raises(PermissionDenied):
        await services.events.tournaments(captain)
    with pytest.raises(PermissionDenied):
        await services.game_payments.matches(captain, squad_id)


async def test_game_payments_locked_to_coach(
    services: Services, squad_id: int, uow_factory: UowFactory
) -> None:
    """Сбор денег капитану не выдать ни через /permissions, ни записью в БД."""
    admin = await ctx_of(services, ADMIN_TG_ID)
    with pytest.raises(Conflict):
        await services.permissions.toggle(admin, SquadRole.CAPTAIN, Permission.GAME_PAYMENTS)
    with pytest.raises(Conflict):
        await services.permissions.toggle(admin, SquadRole.PLAYER, Permission.GAME_PAYMENTS)
    async with uow_factory() as uow:  # «в обход» — права всё равно нет
        await uow.system.save_settings(PERMISSIONS_SCOPE, {"captain": ["game_payments", "duty"]})
        await uow.commit()
    invalidate_permissions_cache()
    captain = await ctx_of(services, CAPTAIN)
    assert not captain.can(squad_id, Permission.GAME_PAYMENTS)
    assert captain.can(squad_id, Permission.DUTY)


async def test_admin_changes_role_permissions(services: Services, squad_id: int) -> None:
    admin = await ctx_of(services, ADMIN_TG_ID)
    perms = await services.permissions.toggle(admin, SquadRole.CAPTAIN, Permission.DUTY)
    assert Permission.DUTY not in perms
    captain = await ctx_of(services, CAPTAIN)
    with pytest.raises(PermissionDenied):
        await services.duty.manager_prompts(captain, squad_id)

    reset = await services.permissions.reset(admin, SquadRole.CAPTAIN)
    assert reset == DEFAULT_ROLE_PERMISSIONS[SquadRole.CAPTAIN]
    assert (await ctx_of(services, CAPTAIN)).can(squad_id, Permission.DUTY)

    coach = await ctx_of(services, COACH)
    with pytest.raises(PermissionDenied):  # менять права — только админ
        await services.permissions.toggle(coach, SquadRole.COACH, Permission.DUTY)
    with pytest.raises(PermissionDenied):  # настройки — только админ
        await services.settings.set_squad_value(coach, squad_id, "duty_count", "3")


async def test_recipients_by_permission(
    services: Services, squad_id: int, uow_factory: UowFactory
) -> None:
    """Кому слать то, что требует действия: по праву; нет никого — админам."""
    admin = await ctx_of(services, ADMIN_TG_ID)
    recipients = services.duty._recipients
    async with uow_factory() as uow:
        duty = await recipients.with_permission(uow, squad_id, Permission.DUTY)
        assert [u.tg_user_id for u in duty] == [CAPTAIN]
        events = await recipients.with_permission(uow, squad_id, Permission.EVENTS)
        assert [u.tg_user_id for u in events] == [COACH]  # разбор после матча — тренеру
        meetings = await recipients.with_permission(uow, squad_id, Permission.MEETINGS)
        assert sorted(u.tg_user_id for u in meetings) == [COACH, CAPTAIN]
    await services.permissions.toggle(admin, SquadRole.CAPTAIN, Permission.DUTY)
    async with uow_factory() as uow:
        fallback = await recipients.with_permission(uow, squad_id, Permission.DUTY)
        assert [u.tg_user_id for u in fallback] == [ADMIN_TG_ID]
