"""Управление админами бота (ФТ-10).

Два источника прав:
- ADMIN_TG_IDS в .env — «корневые» админы: из бота их не снять, так система не останется без админа;
- users.is_admin — админы, назначенные через /admins. Назначить можно только участника состава;
  при выходе из всех составов права снимаются автоматически (ФТ-0).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.schemas.users import UserDTO, profile_link
from pfbc_bot.services.common import to_dto
from pfbc_bot.services.errors import Conflict, NotFound, PermissionDenied
from pfbc_bot.texts import ru


@dataclass(frozen=True, slots=True)
class AdminView:
    tg_user_id: int
    user: UserDTO | None  # None — админ из .env, ещё не писавший боту
    from_config: bool

    @property
    def display_name(self) -> str:
        return self.user.display_name if self.user else f"id{self.tg_user_id}"

    @property
    def profile_html(self) -> str:
        username = self.user.username if self.user else None
        return profile_link(self.tg_user_id, username, self.display_name)


class AdminService:
    def __init__(self, uow_factory: UowFactory, admin_tg_ids: Iterable[int]) -> None:
        self._uow_factory = uow_factory
        self._config_ids = frozenset(admin_tg_ids)

    @staticmethod
    def _require_admin(actor: UserContext) -> None:
        if not actor.is_admin:
            raise PermissionDenied(ru.NO_PERMISSION)

    async def list_admins(self, actor: UserContext) -> list[AdminView]:
        self._require_admin(actor)
        async with self._uow_factory() as uow:
            db_admins = {u.tg_user_id: u for u in await uow.users.list_admins()}
            config_users = {
                tg_id: await uow.users.get_by_tg_id(tg_id) for tg_id in self._config_ids
            }
        views = [
            AdminView(tg_id, to_dto(user) if user else None, from_config=True)
            for tg_id, user in config_users.items()
        ]
        views += [
            AdminView(tg_id, to_dto(user), from_config=False)
            for tg_id, user in db_admins.items()
            if tg_id not in self._config_ids
        ]
        return sorted(views, key=lambda v: (not v.from_config, v.display_name.lower()))

    def is_admin_user(self, user: UserDTO) -> bool:
        return user.is_admin or user.tg_user_id in self._config_ids

    async def grant(self, actor: UserContext, user_id: int) -> UserDTO:
        self._require_admin(actor)
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFound(ru.MEMBER_NOT_FOUND)
            if not await uow.members.list_active_for_user(user_id):
                raise Conflict(ru.ADMIN_NOT_MEMBER)
            if user.is_admin or user.tg_user_id in self._config_ids:
                raise Conflict(ru.ADMIN_ALREADY)
            user.is_admin = True
            await uow.system.audit(
                actor_user_id=actor.user.id, action="admin.grant", entity="user", entity_id=user_id
            )
            await uow.commit()
            return to_dto(user)

    async def revoke(self, actor: UserContext, user_id: int) -> UserDTO:
        self._require_admin(actor)
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is not None and user.tg_user_id in self._config_ids:
                raise Conflict(ru.ADMIN_FROM_CONFIG)
            if user is None or not user.is_admin:
                raise NotFound(ru.ADMIN_NOT_FOUND)
            if user.id == actor.user.id:
                raise Conflict(ru.ADMIN_SELF_REVOKE)
            user.is_admin = False
            await uow.system.audit(
                actor_user_id=actor.user.id, action="admin.revoke", entity="user", entity_id=user_id
            )
            await uow.commit()
            return to_dto(user)
