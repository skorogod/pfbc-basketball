"""Администрирование составов: привязка групп и топиков, роли (ФТ-10)."""

from dataclasses import dataclass
from datetime import datetime

from pfbc_bot.db.models import Squad, SquadChat
from pfbc_bot.domain.enums import MembershipStatus, SquadRole, TopicPurpose
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.squads import MembershipDTO, SquadDTO, SquadMemberView, UserContext
from pfbc_bot.services.common import to_dto
from pfbc_bot.services.duty import DutyService
from pfbc_bot.services.errors import Conflict, NotFound, PermissionDenied
from pfbc_bot.texts import ru


@dataclass(frozen=True, slots=True)
class SquadOverview:
    squad: SquadDTO
    chat_title: str | None
    members: int


class SquadService:
    def __init__(self, uow_factory: UowFactory, duty: DutyService | None = None) -> None:
        self._uow_factory = uow_factory
        self._duty = duty

    @staticmethod
    def _require_admin(actor: UserContext) -> None:
        if not actor.is_admin:
            raise PermissionDenied(ru.NO_PERMISSION)

    async def bind_chat(
        self, actor: UserContext, chat_id: int, chat_title: str | None, squad_name: str
    ) -> SquadDTO:
        self._require_admin(actor)
        squad_name = " ".join(squad_name.split())
        if not squad_name:
            raise Conflict(ru.BIND_USAGE)
        async with self._uow_factory() as uow:
            existing_chat = await uow.squads.get_chat_by_chat_id(chat_id)
            if existing_chat is not None:
                bound = await uow.squads.get(existing_chat.squad_id)
                raise Conflict(ru.BIND_CONFLICT.format(squad=bound.name if bound else "?"))
            squad = await uow.squads.get_by_name(squad_name)
            if squad is None:
                squad = await uow.squads.add(Squad(name=squad_name))
            elif await uow.squads.get_chat_for_squad(squad.id) is not None:
                raise Conflict(ru.BIND_SQUAD_TAKEN.format(squad=squad.name))
            await uow.squads.add_chat(
                SquadChat(squad_id=squad.id, chat_id=chat_id, title=chat_title)
            )
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="squad.bind_chat",
                entity="squad",
                entity_id=squad.id,
                payload={"chat_id": chat_id},
            )
            await uow.commit()
            return SquadDTO.model_validate(squad)

    async def squad_for_chat(self, chat_id: int) -> SquadDTO | None:
        async with self._uow_factory() as uow:
            chat = await uow.squads.get_chat_by_chat_id(chat_id)
            squad = await uow.squads.get(chat.squad_id) if chat else None
            return SquadDTO.model_validate(squad) if squad else None

    async def bind_topic(
        self, actor: UserContext, chat_id: int, purpose: TopicPurpose, thread_id: int | None
    ) -> None:
        self._require_admin(actor)
        async with self._uow_factory() as uow:
            chat = await uow.squads.get_chat_by_chat_id(chat_id)
            if chat is None:
                raise NotFound(ru.CHAT_NOT_BOUND)
            await uow.squads.upsert_topic(chat.squad_id, purpose, thread_id)
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="squad.bind_topic",
                entity="squad",
                entity_id=chat.squad_id,
                payload={"purpose": purpose.value, "thread_id": thread_id},
            )
            await uow.commit()

    async def set_role(
        self,
        actor: UserContext,
        squad_id: int,
        user_id: int,
        role: SquadRole,
        *,
        grant: bool,
        now: datetime | None = None,
    ) -> SquadMemberView:
        """Выдаёт или снимает роль участнику состава. Только для активных участников.
        Тренер не дежурит: назначенный тренером снимается с будущих дежурств."""
        self._require_admin(actor)
        async with self._uow_factory() as uow:
            member = await uow.members.get(user_id, squad_id)
            user = await uow.users.get(user_id)
            if member is None or user is None or member.status != MembershipStatus.ACTIVE:
                raise NotFound(ru.MEMBER_NOT_FOUND)
            if grant:
                await uow.members.add_role(user_id, squad_id, role)
            else:
                await uow.members.remove_roles(user_id, squad_id, [role])
            await uow.system.audit(
                actor_user_id=actor.user.id,
                action="role.grant" if grant else "role.revoke",
                entity="user",
                entity_id=user_id,
                payload={"squad_id": squad_id, "role": role.value},
            )
            await uow.commit()
            roles = (await uow.members.roles_by_squad(user_id, [squad_id])).get(squad_id, set())
            view = SquadMemberView(
                user=to_dto(user),
                membership=MembershipDTO.model_validate(member),
                roles=frozenset(roles),
            )
        if grant and role == SquadRole.COACH and self._duty is not None:
            await self._duty.handle_became_coach(user_id, squad_id, now=now)
        return view

    async def overview(self) -> list[SquadOverview]:
        async with self._uow_factory() as uow:
            result = []
            for squad in await uow.squads.list_active():
                chat = await uow.squads.get_chat_for_squad(squad.id)
                members = await uow.members.list_active_for_squad(squad.id)
                result.append(
                    SquadOverview(
                        squad=SquadDTO.model_validate(squad),
                        chat_title=chat.title if chat else None,
                        members=len(members),
                    )
                )
            return result

    async def get_many(self, squad_ids: list[int]) -> list[SquadDTO]:
        async with self._uow_factory() as uow:
            return [SquadDTO.model_validate(s) for s in await uow.squads.list_by_ids(squad_ids)]

    async def get(self, squad_id: int) -> SquadDTO:
        async with self._uow_factory() as uow:
            squad = await uow.squads.get(squad_id)
            if squad is None:
                raise NotFound("Состав не найден")
            return SquadDTO.model_validate(squad)

    async def members(self, squad_id: int) -> list[SquadMemberView]:
        async with self._uow_factory() as uow:
            roles = await uow.members.roles_in_squad(squad_id)
            return [
                SquadMemberView(
                    user=to_dto(user),
                    membership=MembershipDTO.model_validate(member),
                    roles=frozenset(roles.get(user.id, set())),
                )
                for member, user in await uow.members.list_active_for_squad(squad_id)
            ]
