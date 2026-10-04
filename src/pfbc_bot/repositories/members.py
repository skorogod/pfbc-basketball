from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import SquadChat, SquadMember, SquadRoleAssignment, User
from pfbc_bot.domain.enums import MembershipStatus, SquadRole


class MembersRepository:
    """Членство в составах и роли."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int, squad_id: int) -> SquadMember | None:
        stmt = select(SquadMember).where(
            SquadMember.user_id == user_id, SquadMember.squad_id == squad_id
        )
        return (await self._session.scalars(stmt)).first()

    async def add(self, member: SquadMember) -> SquadMember:
        self._session.add(member)
        await self._session.flush()
        return member

    async def list_active_for_user(self, user_id: int) -> list[SquadMember]:
        stmt = select(SquadMember).where(
            SquadMember.user_id == user_id, SquadMember.status == MembershipStatus.ACTIVE
        )
        return list(await self._session.scalars(stmt))

    async def list_active_for_squad(self, squad_id: int) -> list[tuple[SquadMember, User]]:
        stmt = (
            select(SquadMember, User)
            .join(User, User.id == SquadMember.user_id)
            .where(SquadMember.squad_id == squad_id, SquadMember.status == MembershipStatus.ACTIVE)
            .order_by(User.full_name, User.first_name)
        )
        return [(m, u) for m, u in (await self._session.execute(stmt)).all()]

    async def list_all_active_with_chat(self) -> list[tuple[SquadMember, User, SquadChat]]:
        """Все активные членства с tg_id и чатом — для ежедневной сверки с Telegram."""
        stmt = (
            select(SquadMember, User, SquadChat)
            .join(User, User.id == SquadMember.user_id)
            .join(SquadChat, SquadChat.squad_id == SquadMember.squad_id)
            .where(SquadMember.status == MembershipStatus.ACTIVE)
        )
        return [(m, u, c) for m, u, c in (await self._session.execute(stmt)).all()]

    # --- роли ---
    async def roles_by_squad(
        self, user_id: int, squad_ids: Iterable[int]
    ) -> dict[int, set[SquadRole]]:
        ids = list(squad_ids)
        result: dict[int, set[SquadRole]] = defaultdict(set)
        if not ids:
            return result
        stmt = select(SquadRoleAssignment).where(
            SquadRoleAssignment.user_id == user_id, SquadRoleAssignment.squad_id.in_(ids)
        )
        for row in await self._session.scalars(stmt):
            result[row.squad_id].add(row.role)
        return result

    async def roles_in_squad(self, squad_id: int) -> dict[int, set[SquadRole]]:
        """user_id → роли в составе."""
        result: dict[int, set[SquadRole]] = defaultdict(set)
        stmt = select(SquadRoleAssignment).where(SquadRoleAssignment.squad_id == squad_id)
        for row in await self._session.scalars(stmt):
            result[row.user_id].add(row.role)
        return result

    async def add_role(self, user_id: int, squad_id: int, role: SquadRole) -> None:
        stmt = (
            insert(SquadRoleAssignment)
            .values(user_id=user_id, squad_id=squad_id, role=role)
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)

    async def remove_roles(self, user_id: int, squad_id: int, roles: Iterable[SquadRole]) -> int:
        stmt = delete(SquadRoleAssignment).where(
            SquadRoleAssignment.user_id == user_id,
            SquadRoleAssignment.squad_id == squad_id,
            SquadRoleAssignment.role.in_(list(roles)),
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def users_with_roles(self, squad_id: int, roles: Iterable[SquadRole]) -> list[User]:
        """Пользователи с ролями в составе, только при активном членстве."""
        stmt = (
            select(User)
            .join(SquadRoleAssignment, SquadRoleAssignment.user_id == User.id)
            .join(
                SquadMember,
                (SquadMember.user_id == User.id) & (SquadMember.squad_id == squad_id),
            )
            .where(
                SquadRoleAssignment.squad_id == squad_id,
                SquadRoleAssignment.role.in_(list(roles)),
                SquadMember.status == MembershipStatus.ACTIVE,
            )
            .distinct()
        )
        return list(await self._session.scalars(stmt))
