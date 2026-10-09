"""Права ролей (что может тренер, капитан, игрок) и адресаты действий по праву."""

import time
from collections.abc import Iterable

from pfbc_bot.db.models import User
from pfbc_bot.domain.enums import SquadRole
from pfbc_bot.domain.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_TITLES,
    Permission,
    allowed_for,
    merge_role_permissions,
    permissions_for,
)
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.common import send_direct
from pfbc_bot.services.errors import Conflict, PermissionDenied
from pfbc_bot.services.ports import Keyboard, Notifier
from pfbc_bot.texts import ru

PERMISSIONS_SCOPE = "permissions"


def require(actor: UserContext, squad_id: int, permission: Permission) -> None:
    """Действие в составе — только с правом (админ может всё)."""
    if not actor.can(squad_id, permission):
        raise PermissionDenied(ru.NO_PERMISSION_FOR.format(title=PERMISSION_TITLES[permission]))


# Таблица прав читается на каждом апдейте — держим её в памяти процесса недолго. Изменение
# через /permissions сбрасывает кеш сразу (в этом процессе); worker увидит его за CACHE_TTL.
CACHE_TTL = 30.0
_cache: tuple[float, dict[SquadRole, frozenset[Permission]]] | None = None


def invalidate_permissions_cache() -> None:
    global _cache
    _cache = None


async def load_role_permissions(uow: UnitOfWork) -> dict[SquadRole, frozenset[Permission]]:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL:
        return _cache[1]
    table = merge_role_permissions(await uow.system.get_settings(PERMISSIONS_SCOPE))
    _cache = (now, table)
    return table


class Recipients:
    """Кому слать то, что требует действия: участникам состава с нужным правом; нет таких —
    админам-участникам состава, а если и их нет — корневым админам из ADMIN_TG_IDS."""

    def __init__(self, admin_tg_ids: Iterable[int]) -> None:
        self._admin_tg_ids = frozenset(admin_tg_ids)

    def is_admin(self, user: User) -> bool:
        return user.is_admin or user.tg_user_id in self._admin_tg_ids

    async def with_permission(
        self, uow: UnitOfWork, squad_id: int, permission: Permission
    ) -> list[User]:
        table = await load_role_permissions(uow)
        roles = await uow.members.roles_in_squad(squad_id)
        members = [user for _, user in await uow.members.list_active_for_squad(squad_id)]
        holders = [
            u
            for u in members
            if permission in permissions_for(frozenset(roles.get(u.id, set())), table)
        ]
        if holders:
            return holders
        admins = [u for u in members if self.is_admin(u)]
        if admins:
            return admins
        return [u for u in await uow.users.list_by_tg_ids(sorted(self._admin_tg_ids))]

    async def notify(
        self,
        uow: UnitOfWork,
        notifier: Notifier,
        squad_id: int,
        permission: Permission,
        text: str,
        *,
        keyboard: Keyboard | None = None,
    ) -> int:
        """Личные сообщения тем, кто может выполнить действие. Возвращает число доставленных."""
        delivered = 0
        for user in await self.with_permission(uow, squad_id, permission):
            delivered += await send_direct(notifier, user, text, keyboard=keyboard)
        return delivered


class PermissionService:
    def __init__(self, uow_factory: UowFactory) -> None:
        self._uow_factory = uow_factory

    @staticmethod
    def _require_admin(actor: UserContext) -> None:
        if not actor.is_admin:
            raise PermissionDenied(ru.NO_PERMISSION)

    async def role_permissions(self, actor: UserContext) -> dict[SquadRole, frozenset[Permission]]:
        self._require_admin(actor)
        async with self._uow_factory() as uow:
            return await load_role_permissions(uow)

    async def toggle(
        self, actor: UserContext, role: SquadRole, permission: Permission
    ) -> frozenset[Permission]:
        """Включает или выключает право у роли; возвращает новые права роли."""
        self._require_admin(actor)
        if not allowed_for(role, permission):
            raise Conflict(ru.PERMISSION_LOCKED)
        async with self._uow_factory() as uow:
            saved = await uow.system.get_settings(PERMISSIONS_SCOPE)
            current = merge_role_permissions(saved)[role]
            updated = current - {permission} if permission in current else current | {permission}
            saved[role.value] = sorted(p.value for p in updated)
            await uow.system.save_settings(PERMISSIONS_SCOPE, saved)
            invalidate_permissions_cache()
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="permissions.toggle",
                entity="role",
                entity_id=role.value,
                payload={"permission": permission.value, "enabled": permission in updated},
            )
            await uow.commit()
            return updated

    async def reset(self, actor: UserContext, role: SquadRole) -> frozenset[Permission]:
        """Права роли — по умолчанию."""
        self._require_admin(actor)
        async with self._uow_factory() as uow:
            saved = await uow.system.get_settings(PERMISSIONS_SCOPE)
            saved.pop(role.value, None)
            await uow.system.save_settings(PERMISSIONS_SCOPE, saved)
            invalidate_permissions_cache()
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="permissions.reset",
                entity="role",
                entity_id=role.value,
            )
            await uow.commit()
            return DEFAULT_ROLE_PERMISSIONS[role]
