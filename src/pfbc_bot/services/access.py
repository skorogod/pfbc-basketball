"""Контроль доступа (ФТ-0): кто может работать с ботом и с какими правами."""

from collections.abc import Iterable

from pfbc_bot.db.models import User
from pfbc_bot.domain.enums import SquadRole, UserStatus
from pfbc_bot.domain.permissions import permissions_for
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.schemas.users import TelegramUser
from pfbc_bot.services.common import to_dto
from pfbc_bot.services.permissions import load_role_permissions


class AccessService:
    def __init__(self, uow_factory: UowFactory, admin_tg_ids: Iterable[int]) -> None:
        self._uow_factory = uow_factory
        self._admin_tg_ids = frozenset(admin_tg_ids)

    def is_config_admin(self, tg_user_id: int) -> bool:
        return tg_user_id in self._admin_tg_ids

    async def load_context(self, tg_user: TelegramUser) -> UserContext | None:
        """Права пользователя по БД. None — пользователь системе неизвестен.

        Админ = ID в ADMIN_TG_IDS (вычисляется на лету, в БД не пишется — удаление из .env сразу
        снимает права) или флаг users.is_admin (назначен через /admins).
        """
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_tg_id(tg_user.tg_user_id)
            config_admin = self.is_config_admin(tg_user.tg_user_id)
            created = user is None
            if user is None:
                if not config_admin:
                    return None
                user = await uow.users.add(
                    User(
                        tg_user_id=tg_user.tg_user_id,
                        username=tg_user.username,
                        first_name=tg_user.first_name,
                        status=UserStatus.UNREGISTERED,
                    )
                )
            changed = self._sync_profile(user, tg_user) or created

            memberships = await uow.members.list_active_for_user(user.id)
            squad_ids = [m.squad_id for m in memberships]
            roles = await uow.members.roles_by_squad(user.id, squad_ids)
            table = await load_role_permissions(uow)
            if changed:
                await uow.commit()
            squad_roles = {sid: frozenset(roles.get(sid, {SquadRole.PLAYER})) for sid in squad_ids}
            return UserContext(
                user=to_dto(user),
                is_admin=user.is_admin or config_admin,
                roles=squad_roles,
                permissions={sid: permissions_for(r, table) for sid, r in squad_roles.items()},
            )

    @staticmethod
    def _sync_profile(user: User, tg_user: TelegramUser) -> bool:
        changed = False
        if user.username != tg_user.username:
            user.username = tg_user.username
            changed = True
        if tg_user.first_name and user.first_name != tg_user.first_name:
            user.first_name = tg_user.first_name
            changed = True
        return changed
