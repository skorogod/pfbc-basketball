"""ФТ-0: членство в группе — источник правды; доступ, уход, повторное вступление."""

from datetime import UTC, datetime, timedelta

from pfbc_bot.domain.enums import MembershipStatus, SquadRole, UserStatus
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.services.container import Services

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, ctx_of, join, setup_squad, tg

T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


async def test_stranger_has_no_access(services: Services) -> None:
    await setup_squad(services)
    assert await services.access.load_context(tg(1)) is None


async def test_config_admin_has_access_without_membership(services: Services) -> None:
    ctx = await ctx_of(services, ADMIN_TG_ID)
    assert ctx.is_admin and ctx.is_allowed and not ctx.is_member


async def test_join_grants_access_silently(services: Services, notifier: FakeNotifier) -> None:
    await setup_squad(services)
    result = await services.membership.on_join(CHAT_ID, tg(1), now=T0)
    assert result is not None and result.first_time_in_squad and not result.user.is_registered
    assert notifier.messages == []  # в группе бот молчит
    ctx = await ctx_of(services, 1)
    assert ctx.is_member and ctx.has_role(result.squad.id, SquadRole.PLAYER)


async def test_join_to_unbound_chat_is_ignored(services: Services) -> None:
    assert await services.membership.on_join(-1, tg(1), now=T0) is None
    assert await services.access.load_context(tg(1)) is None


async def test_leave_revokes_access_staff_roles_and_notifies_admin(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    squad = await setup_squad(services)
    await join(services, 1, at=T0, role=SquadRole.CAPTAIN)

    await services.membership.on_leave(CHAT_ID, 1, removed=True, now=T0 + timedelta(days=1))

    ctx = await services.access.load_context(tg(1))
    assert ctx is not None and not ctx.is_allowed
    async with uow_factory() as uow:
        user = await uow.users.get_by_tg_id(1)
        assert user is not None and user.status == UserStatus.INACTIVE
        member = await uow.members.get(user.id, squad.id)
        assert member is not None and member.status == MembershipStatus.REMOVED
        roles = await uow.members.roles_by_squad(user.id, [squad.id])
        assert SquadRole.CAPTAIN not in roles.get(squad.id, set())
    assert any("капитан" in m.text for m in notifier.to(ADMIN_TG_ID))


async def test_rejoin_restores_access_and_keeps_first_joined_at(
    services: Services, uow_factory: UowFactory
) -> None:
    squad = await setup_squad(services)
    await join(services, 1, at=T0)
    await services.membership.on_leave(CHAT_ID, 1, removed=False, now=T0 + timedelta(days=5))
    result = await services.membership.on_join(CHAT_ID, tg(1), now=T0 + timedelta(days=30))

    assert result is not None and not result.first_time_in_squad
    async with uow_factory() as uow:
        user = await uow.users.get_by_tg_id(1)
        assert user is not None
        member = await uow.members.get(user.id, squad.id)
        assert member is not None
        assert member.status == MembershipStatus.ACTIVE
        assert member.first_joined_at == T0
        assert member.joined_at == T0 + timedelta(days=30)
    assert (await ctx_of(services, 1)).is_allowed


async def test_member_who_joined_before_bot_is_verified_via_telegram(
    services: Services, notifier: FakeNotifier
) -> None:
    await setup_squad(services)
    notifier.chat_members[CHAT_ID] = {5}
    assert await services.membership.verify_via_telegram(tg(5)) is True
    assert (await ctx_of(services, 5)).is_member
    assert await services.membership.verify_via_telegram(tg(6)) is False
    notifier.chat_members[CHAT_ID].add(6)
    # отрицательный результат закеширован — повторная проверка не идёт в Telegram
    assert await services.membership.verify_via_telegram(tg(6)) is False


async def test_reconcile_closes_missed_leaves(services: Services, notifier: FakeNotifier) -> None:
    await setup_squad(services)
    await join(services, 1, at=T0)
    await join(services, 2, at=T0)
    notifier.chat_members[CHAT_ID] = {1}  # пользователь 2 покинул группу, апдейт потерян

    fixed = await services.membership.reconcile(pause=0)

    assert fixed == 1
    assert (await ctx_of(services, 1)).is_member
    ctx2 = await services.access.load_context(tg(2))
    assert ctx2 is not None and not ctx2.is_allowed


async def test_anonymize_after_retention(services: Services, uow_factory: UowFactory) -> None:
    await setup_squad(services)
    await join(services, 1, at=T0)
    await services.membership.on_leave(CHAT_ID, 1, removed=False, now=T0)
    assert await services.membership.anonymize_expired(now=T0 + timedelta(days=366)) == 1
    async with uow_factory() as uow:
        user = await uow.users.get_by_tg_id(1)
        assert user is not None
        assert user.status == UserStatus.ANONYMIZED and user.first_name is None
