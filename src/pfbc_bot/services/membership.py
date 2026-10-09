"""Синхронизация членства в составах с Telegram-группами — источник правды (ФТ-0).

Списка участников группы Bot API не даёт. Бот узнаёт участника:
- по событию вступления (chat_member) — с настоящей датой вступления;
- если тот был в группе до подключения бота («найденный» участник): написал боту /start,
  ответил в опросе бота (проверка через getChatMember) или написал в группе (уже участник).
  Дата его вступления неизвестна, поэтому новичком (ФТ-3) он не считается.
"""

import asyncio
import html
import time as monotonic_time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
from pfbc_bot.services.duty import DutyService
from pfbc_bot.services.ports import Notifier
from pfbc_bot.services.settings import load_global_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)

# «Найденный» участник был в группе до подключения бота: настоящая дата вступления неизвестна,
# но он точно не новичок — иначе после запуска бота вся команда 14 дней не дежурила бы.
DISCOVERED_FIRST_JOINED_AT = datetime(2000, 1, 1, tzinfo=UTC)


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
        duty: DutyService | None = None,
        negative_cache_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier
        self._duty = duty
        self._admin_tg_ids = frozenset(admin_tg_ids)
        self._negative_ttl = negative_cache_ttl.total_seconds()
        self._negative_cache: dict[int, float] = {}
        self._negative_chat_cache: dict[tuple[int, int], float] = {}

    # --- события Telegram ---
    async def on_join(
        self,
        chat_id: int,
        tg_user: TelegramUser,
        *,
        now: datetime | None = None,
        discovered: bool = False,
    ) -> JoinResult | None:
        """Вступление в группу. discovered — участник найден, а не замечен при вступлении."""
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
            first_time = await self._activate(uow, user, squad.id, now, discovered=discovered)
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
            left = await self._deactivate(uow, user, chat.squad_id, removed=removed, now=now)
            await uow.commit()
            user_id, squad_id = user.id, chat.squad_id
        if left:
            await self._release_duties(user_id, squad_id, now)

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
                await self._activate(uow, user, squad_id, now, discovered=True)
            await uow.commit()
        return True

    async def verify_in_chat(
        self, chat_id: int, tg_user: TelegramUser, *, now: datetime | None = None
    ) -> bool:
        """Незнакомец ответил в опросе бота в группе chat_id: если Telegram подтверждает, что он
        участник группы, — заводим. Отрицательный ответ кешируется (на пару пользователь–чат)."""
        if tg_user.is_bot:
            return False
        key = (tg_user.tg_user_id, chat_id)
        expires = self._negative_chat_cache.get(key)
        if expires and expires > monotonic_time.monotonic():
            return False
        if not await self._notifier.is_chat_member(chat_id, tg_user.tg_user_id):
            self._negative_chat_cache[key] = monotonic_time.monotonic() + self._negative_ttl
            return False
        joined = await self.on_join(chat_id, tg_user, now=now, discovered=True)
        if joined is not None:
            log.info("member_discovered", user_id=joined.user.id, squad_id=joined.squad.id)
        return joined is not None

    async def on_group_message(
        self,
        chat_id: int,
        tg_user: TelegramUser,
        known_squad_ids: Iterable[int],
        *,
        now: datetime | None = None,
    ) -> bool:
        """Автор сообщения в группе состава — её участник. Если бот не знал о его членстве в этом
        составе (вступил до подключения бота), — заводим. True — членство только что заведено."""
        async with self._uow_factory() as uow:
            chat = await uow.squads.get_chat_by_chat_id(chat_id)
        if chat is None or chat.squad_id in set(known_squad_ids):
            return False
        joined = await self.on_join(chat_id, tg_user, now=now, discovered=True)
        if joined is not None:
            log.info("member_discovered", user_id=joined.user.id, squad_id=joined.squad.id)
        return joined is not None

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
        left: list[tuple[int, int]] = []
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
                    left.append((user.id, squad_id))
                    await uow.system.audit(
                        actor_user_id=None,
                        action="membership.reconciled",
                        entity="squad_member",
                        entity_id=member.id,
                    )
                    fixed += 1
                await uow.commit()
        for user_id, squad_id in left:
            await self._release_duties(user_id, squad_id, now)
        log.info("membership_reconciled", checked=len(rows), fixed=fixed)
        return fixed

    async def _release_duties(self, user_id: int, squad_id: int, now: datetime) -> None:
        """Вышедший из группы исключается из очереди и снимается с будущих дежурств (ФТ-0)."""
        if self._duty is not None:
            await self._duty.handle_member_left(user_id, squad_id, now=now)

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
    async def _activate(
        uow: UnitOfWork, user: User, squad_id: int, now: datetime, *, discovered: bool = False
    ) -> bool:
        """Активирует членство. True — пользователь впервые в этом составе."""
        member = await uow.members.get(user.id, squad_id)
        first_time = member is None
        if member is None:
            await uow.members.add(
                SquadMember(
                    user_id=user.id,
                    squad_id=squad_id,
                    status=MembershipStatus.ACTIVE,
                    first_joined_at=DISCOVERED_FIRST_JOINED_AT if discovered else now,
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
    ) -> bool:
        """Закрывает активное членство. True — было активным и закрыто."""
        member = await uow.members.get(user.id, squad_id)
        if member is None or member.status != MembershipStatus.ACTIVE:
            return False
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
                    name=to_dto(user).profile_html,
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
                    ru.ADMIN_LOST_MEMBERSHIP.format(name=to_dto(user).profile_html),
                )
        await uow.system.audit(
            actor_user_id=None,
            action="membership.removed" if removed else "membership.left",
            entity="squad_member",
            entity_id=member.id,
            payload={"squad_id": squad_id, "staff_roles_removed": sorted(staff_roles)},
        )
        log.info("member_left", user_id=user.id, squad_id=squad_id, removed=removed)
        return True


def anonymize(user: User) -> None:
    """Удаляет персональные данные профиля, оставляя технический идентификатор."""
    user.full_name = None
    user.birth_date = None
    user.mtgame_url = None
    user.phone = None
    user.payment_details = None
    user.username = None
    user.first_name = None
    user.pd_consent_at = None
    user.guardian_consent = False
