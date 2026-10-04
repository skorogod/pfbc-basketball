"""Синхронизация членства в составах с Telegram-группами — источник правды (ФТ-0)."""

import asyncio
import html
import time as monotonic_time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pfbc_bot.db.models import SquadMember, User
from pfbc_bot.domain.dates import utcnow
from pfbc_bot.domain.enums import (
    ROLE_TITLES,
    STAFF_ROLES,
    MembershipStatus,
    SquadRole,
    UserStatus,
)
from pfbc_bot.logging import get_logger
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.squads import SquadDTO
from pfbc_bot.schemas.users import TelegramUser, UserDTO
from pfbc_bot.services.common import notify_admins, to_dto
from pfbc_bot.services.ports import Notifier
from pfbc_bot.services.settings import load_global_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class JoinResult:
    user: UserDTO
    squad: SquadDTO
    first_time_in_squad: bool


class MembershipService:
    def __init__(
        self,
        uow_factory: UowFactory,
        notifier: Notifier,
        admin_tg_ids: Iterable[int],
        *,
        negative_cache_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier
        self._admin_tg_ids = frozenset(admin_tg_ids)
        self._negative_ttl = negative_cache_ttl.total_seconds()
        self._negative_cache: dict[int, float] = {}

    # --- события Telegram ---
    async def on_join(
        self, chat_id: int, tg_user: TelegramUser, *, now: datetime | None = None
    ) -> JoinResult | None:
        if tg_user.is_bot:
            return None
        now = now or utcnow()
        async with self._uow_factory() as uow:
            chat = await uow.squads.get_chat_by_chat_id(chat_id)
            if chat is None:
                return None
            squad = await uow.squads.get(chat.squad_id)
            if squad is None:
                return None
            user = await self._get_or_create_user(uow, tg_user)
            first_time = await self._activate(uow, user, squad.id, now)
            await uow.commit()
            self._negative_cache.pop(tg_user.tg_user_id, None)
            log.info("member_joined", user_id=user.id, squad_id=squad.id, first_time=first_time)
            return JoinResult(
                user=to_dto(user),
                squad=SquadDTO.model_validate(squad),
                first_time_in_squad=first_time,
            )

    async def on_leave(
        self, chat_id: int, tg_user_id: int, *, removed: bool, now: datetime | None = None
    ) -> None:
        now = now or utcnow()
        async with self._uow_factory() as uow:
            chat = await uow.squads.get_chat_by_chat_id(chat_id)
            user = await uow.users.get_by_tg_id(tg_user_id)
            if chat is None or user is None:
                return
            await self._deactivate(uow, user, chat.squad_id, removed=removed, now=now)
            await uow.commit()

    # --- доступ пользователей, вступивших до подключения бота ---
    async def verify_via_telegram(
        self, tg_user: TelegramUser, *, now: datetime | None = None
    ) -> bool:
        """Проверка через getChatMember во всех группах составов. Отрицательный ответ кешируется."""
        expires = self._negative_cache.get(tg_user.tg_user_id)
        if expires and expires > monotonic_time.monotonic():
            return False
        now = now or utcnow()
        async with self._uow_factory() as uow:
            chats = await uow.squads.list_chats()
        found = [
            c.squad_id
            for c in chats
            if await self._notifier.is_chat_member(c.chat_id, tg_user.tg_user_id)
        ]
        if not found:
            self._negative_cache[tg_user.tg_user_id] = (
                monotonic_time.monotonic() + self._negative_ttl
            )
            return False
        async with self._uow_factory() as uow:
            user = await self._get_or_create_user(uow, tg_user)
            for squad_id in found:
                await self._activate(uow, user, squad_id, now)
            await uow.commit()
        return True

    # --- плановые задачи ---
    async def reconcile(self, *, now: datetime | None = None, pause: float = 0.05) -> int:
        """Ежедневная сверка: активные членства без фактического членства в группе закрываются."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            rows = [
                (m.squad_id, u.tg_user_id, c.chat_id)
                for m, u, c in await uow.members.list_all_active_with_chat()
            ]
        fixed = 0
        for squad_id, tg_user_id, chat_id in rows:
            is_member = await self._notifier.is_chat_member(chat_id, tg_user_id)
            await asyncio.sleep(pause)
            async with self._uow_factory() as uow:
                user = await uow.users.get_by_tg_id(tg_user_id)
                if user is None:
                    continue
                member = await uow.members.get(user.id, squad_id)
                if member is None:
                    continue
                if is_member is None:
                    continue  # не удалось проверить — не трогаем
                if is_member:
                    member.last_verified_at = now
                else:
                    await self._deactivate(uow, user, squad_id, removed=False, now=now)
                    await uow.system.audit(
                        actor_user_id=None,
                        action="membership.reconciled",
                        entity="squad_member",
                        entity_id=member.id,
                    )
                    fixed += 1
                await uow.commit()
        log.info("membership_reconciled", checked=len(rows), fixed=fixed)
        return fixed

    async def anonymize_expired(self, *, now: datetime | None = None) -> int:
        now = now or utcnow()
        async with self._uow_factory() as uow:
            settings = await load_global_settings(uow)
            cutoff = now - timedelta(days=settings.inactive_retention_days)
            users = await uow.users.list_inactive_since_before(cutoff)
            for user in users:
                anonymize(user)
                user.status = UserStatus.ANONYMIZED
                await uow.system.audit(
                    actor_user_id=None, action="user.anonymized", entity="user", entity_id=user.id
                )
            await uow.commit()
            return len(users)

    # --- внутреннее ---
    @staticmethod
    async def _get_or_create_user(uow: UnitOfWork, tg_user: TelegramUser) -> User:
        user = await uow.users.get_by_tg_id(tg_user.tg_user_id)
        if user is None:
            user = await uow.users.add(
                User(
                    tg_user_id=tg_user.tg_user_id,
                    username=tg_user.username,
                    first_name=tg_user.first_name,
                    status=UserStatus.UNREGISTERED,
                )
            )
        else:
            user.username = tg_user.username
            user.first_name = tg_user.first_name or user.first_name
        return user

    @staticmethod
    async def _activate(uow: UnitOfWork, user: User, squad_id: int, now: datetime) -> bool:
        """Активирует членство. True — пользователь впервые в этом составе."""
        member = await uow.members.get(user.id, squad_id)
        first_time = member is None
        if member is None:
            await uow.members.add(
                SquadMember(
                    user_id=user.id,
                    squad_id=squad_id,
                    status=MembershipStatus.ACTIVE,
                    first_joined_at=now,
                    joined_at=now,
                    last_verified_at=now,
                )
            )
        elif member.status != MembershipStatus.ACTIVE:
            member.status = MembershipStatus.ACTIVE
            member.joined_at = now
            member.left_at = None
            member.last_verified_at = now
        await uow.members.add_role(user.id, squad_id, SquadRole.PLAYER)
        if user.status in {UserStatus.INACTIVE, UserStatus.ANONYMIZED}:
            user.status = UserStatus.ACTIVE if user.pd_consent_at else UserStatus.UNREGISTERED
            user.inactive_since = None
        return first_time

    async def _deactivate(
        self, uow: UnitOfWork, user: User, squad_id: int, *, removed: bool, now: datetime
    ) -> None:
        member = await uow.members.get(user.id, squad_id)
        if member is None or member.status != MembershipStatus.ACTIVE:
            return
        member.status = MembershipStatus.REMOVED if removed else MembershipStatus.LEFT
        member.left_at = now

        held = (await uow.members.roles_by_squad(user.id, [squad_id])).get(squad_id, set())
        staff_roles = held & STAFF_ROLES
        if staff_roles:
            await uow.members.remove_roles(user.id, squad_id, staff_roles)
            squad = await uow.squads.get(squad_id)
            await notify_admins(
                uow,
                self._notifier,
                self._admin_tg_ids,
                ru.STAFF_LEFT_ADMIN_NOTICE.format(
                    name=html.escape(to_dto(user).display_name),
                    squad=html.escape(squad.name if squad else str(squad_id)),
                    roles=", ".join(ROLE_TITLES[r] for r in sorted(staff_roles)),
                ),
            )

        if not await uow.members.list_active_for_user(user.id):
            if user.status in {UserStatus.ACTIVE, UserStatus.UNREGISTERED}:
                user.status = UserStatus.INACTIVE
                user.inactive_since = now
            if user.is_admin:
                # права админа, выданные через /admins, держатся на членстве в составах (ФТ-0);
                # админы из ADMIN_TG_IDS от этого не зависят
                user.is_admin = False
                await notify_admins(
                    uow,
                    self._notifier,
                    self._admin_tg_ids,
                    ru.ADMIN_LOST_MEMBERSHIP.format(name=html.escape(to_dto(user).display_name)),
                )
        await uow.system.audit(
            actor_user_id=None,
            action="membership.removed" if removed else "membership.left",
            entity="squad_member",
            entity_id=member.id,
            payload={"squad_id": squad_id, "staff_roles_removed": sorted(staff_roles)},
        )
        log.info("member_left", user_id=user.id, squad_id=squad_id, removed=removed)


def anonymize(user: User) -> None:
    """Удаляет персональные данные профиля, оставляя технический идентификатор."""
    user.full_name = None
    user.birth_date = None
    user.mtgame_url = None
    user.username = None
    user.first_name = None
    user.pd_consent_at = None
    user.guardian_consent = False
