"""ФТ-0: членство в группе — источник правды; доступ, уход, повторное вступление."""

from datetime import UTC, date, datetime, time, timedelta

from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import MembershipStatus, PollType, SquadRole, UserStatus
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.trainings import TrainingCreate
from pfbc_bot.services.container import Services
from pfbc_bot.services.membership import DISCOVERED_FIRST_JOINED_AT

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import CHAT_ID, ctx_of, join, setup_squad, tg

T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
SECOND_CHAT = -100600


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


async def _training_poll(
    services: Services, uow_factory: UowFactory, squad_id: int, chat_id: int
) -> str:
    """Тренировка через неделю и опубликованный опрос на неё; возвращает tg_poll_id."""
    admin = await ctx_of(services, ADMIN_TG_ID)
    starts = local_to_utc(date(2026, 10, 12), time(19), "Europe/Moscow")
    training = await services.trainings.add_one_off(
        admin,
        TrainingCreate(squad_id=squad_id, starts_at=starts, venue="Зал"),
        now=starts - timedelta(days=7),
    )
    assert await services.polls.publish_training_polls(now=starts - timedelta(hours=10)) == 1
    async with uow_factory() as uow:
        poll = await uow.polls.get_for_ref(PollType.TRAINING, "training", training.id)
        assert poll is not None and poll.chat_id == chat_id
        return poll.tg_poll_id


async def test_poll_vote_discovers_member_who_joined_before_bot(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    squad = await setup_squad(services)
    poll_id = await _training_poll(services, uow_factory, squad.id, CHAT_ID)
    notifier.chat_members[CHAT_ID] = {7}  # в группе с давних пор, бот о нём не знает

    await services.polls.record_answer(poll_id, 7, [0], profile=tg(7, "Пётр"))
    async with uow_factory() as uow:  # имя — из ответа на опрос
        user = await uow.users.get_by_tg_id(7)
        assert user is not None and user.first_name == "Пётр"
    ctx = await ctx_of(services, 7)
    assert ctx.is_member
    async with uow_factory() as uow:
        member = await uow.members.get(ctx.user.id, squad.id)
        assert member is not None
        # дата вступления неизвестна — новичком не считается (иначе 14 дней без дежурств)
        assert member.first_joined_at == DISCOVERED_FIRST_JOINED_AT
        poll = await uow.polls.get_by_tg_id(poll_id)
        assert poll is not None
        assert await uow.polls.answers(poll.id) == {ctx.user.id: [0]}


async def test_poll_vote_from_outsider_is_ignored_and_cached(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    squad = await setup_squad(services)
    poll_id = await _training_poll(services, uow_factory, squad.id, CHAT_ID)

    await services.polls.record_answer(poll_id, 8, [0])
    assert await services.access.load_context(tg(8)) is None
    # отрицательный ответ закеширован: повторный голос не идёт в Telegram
    notifier.chat_members[CHAT_ID] = {8}
    await services.polls.record_answer(poll_id, 8, [1])
    assert await services.access.load_context(tg(8)) is None


async def test_poll_vote_discovers_membership_in_second_squad(
    services: Services, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    """Игрок известен боту по одному составу, а в группе второго был ещё до бота."""
    await setup_squad(services)
    await join(services, 1, at=T0)
    admin = await ctx_of(services, ADMIN_TG_ID)
    youth = await services.squads.bind_chat(admin, SECOND_CHAT, "PFBC Юниоры", "Юниоры")
    poll_id = await _training_poll(services, uow_factory, youth.id, SECOND_CHAT)
    notifier.chat_members[SECOND_CHAT] = {1}

    await services.polls.record_answer(poll_id, 1, [0])
    assert set((await ctx_of(services, 1)).squad_ids) == {1, youth.id}
