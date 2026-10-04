"""ФТ-10: несколько админов — из ADMIN_TG_IDS и назначенные через /admins."""

from datetime import UTC, datetime

import pytest

from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.services.container import Services, build_services
from pfbc_bot.services.errors import Conflict, PermissionDenied

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, ctx_of, join, setup_squad, tg

T0 = datetime(2026, 1, 10, tzinfo=UTC)
SECOND_CONFIG_ADMIN = 998


async def test_several_admins_from_config(uow_factory: UowFactory, notifier: FakeNotifier) -> None:
    services = build_services(uow_factory, notifier, [ADMIN_TG_ID, SECOND_CONFIG_ADMIN])
    assert (await ctx_of(services, ADMIN_TG_ID)).is_admin
    assert (await ctx_of(services, SECOND_CONFIG_ADMIN)).is_admin
    admins = await services.admins.list_admins(await ctx_of(services, ADMIN_TG_ID))
    assert {a.tg_user_id for a in admins} == {ADMIN_TG_ID, SECOND_CONFIG_ADMIN}
    assert all(a.from_config for a in admins)


async def test_removing_id_from_config_revokes_rights(
    services: Services, uow_factory: UowFactory, notifier: FakeNotifier
) -> None:
    assert (await ctx_of(services, ADMIN_TG_ID)).is_admin
    # тот же пользователь после удаления из ADMIN_TG_IDS (перезапуск с новым .env)
    restarted = build_services(uow_factory, notifier, [])
    ctx = await restarted.access.load_context(tg(ADMIN_TG_ID))
    assert ctx is not None and not ctx.is_admin and not ctx.is_allowed


async def test_grant_and_revoke_admin(services: Services, notifier: FakeNotifier) -> None:
    await setup_squad(services)
    await join(services, 1, at=T0)
    await join(services, 2, at=T0)
    root = await ctx_of(services, ADMIN_TG_ID)
    user1 = (await ctx_of(services, 1)).user

    with pytest.raises(PermissionDenied):
        await services.admins.grant(await ctx_of(services, 2), user1.id)

    await services.admins.grant(root, user1.id)
    new_admin = await ctx_of(services, 1)
    assert new_admin.is_admin
    with pytest.raises(Conflict):
        await services.admins.grant(root, user1.id)

    # назначенный админ может назначать других, но не может снять себя или админа из .env
    user2 = (await ctx_of(services, 2)).user
    await services.admins.grant(new_admin, user2.id)
    with pytest.raises(Conflict):
        await services.admins.revoke(new_admin, new_admin.user.id)
    with pytest.raises(Conflict):
        await services.admins.revoke(new_admin, root.user.id)

    await services.admins.revoke(new_admin, user2.id)
    assert not (await ctx_of(services, 2)).is_admin
    names = {a.display_name for a in await services.admins.list_admins(root)}
    assert names == {root.user.display_name, user1.display_name}


async def test_admin_loses_rights_when_leaving_all_squads(
    services: Services, notifier: FakeNotifier
) -> None:
    await setup_squad(services)
    await join(services, 1, at=T0)
    await services.admins.grant(
        await ctx_of(services, ADMIN_TG_ID), (await ctx_of(services, 1)).user.id
    )

    await services.membership.on_leave(CHAT_ID, 1, removed=False, now=T0)

    ctx = await services.access.load_context(tg(1))
    assert ctx is not None and not ctx.is_admin and not ctx.is_allowed
    assert any("права админа сняты" in m.text for m in notifier.to(ADMIN_TG_ID))


async def test_only_members_can_become_admins(services: Services) -> None:
    await setup_squad(services)
    await join(services, 1, at=T0)
    user_id = (await ctx_of(services, 1)).user.id
    await services.membership.on_leave(CHAT_ID, 1, removed=False, now=T0)
    with pytest.raises(Conflict):
        await services.admins.grant(await ctx_of(services, ADMIN_TG_ID), user_id)
