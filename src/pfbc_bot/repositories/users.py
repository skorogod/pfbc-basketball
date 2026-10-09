from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import SquadMember, User
from pfbc_bot.domain.enums import MembershipStatus, UserStatus


class UsersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_tg_id(self, tg_user_id: int) -> User | None:
        return (
            await self._session.scalars(select(User).where(User.tg_user_id == tg_user_id))
        ).first()

    async def add(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        return user

    async def list_by_ids(self, user_ids: Sequence[int]) -> list[User]:
        if not user_ids:
            return []
        result = await self._session.scalars(select(User).where(User.id.in_(user_ids)))
        return list(result)

    async def list_by_tg_ids(self, tg_user_ids: Sequence[int]) -> list[User]:
        if not tg_user_ids:
            return []
        stmt = select(User).where(User.tg_user_id.in_(list(tg_user_ids)))
        return list(await self._session.scalars(stmt))

    async def list_admins(self) -> list[User]:
        return list(await self._session.scalars(select(User).where(User.is_admin.is_(True))))

    async def list_inactive_since_before(self, cutoff: datetime) -> list[User]:
        stmt = select(User).where(User.status == UserStatus.INACTIVE, User.inactive_since < cutoff)
        return list(await self._session.scalars(stmt))

    async def list_unregistered_members(self) -> list[User]:
        """Незарегистрированные пользователи, у которых есть активное членство."""
        stmt = select(User).where(
            User.status == UserStatus.UNREGISTERED,
            select(SquadMember.id)
            .where(
                SquadMember.user_id == User.id,
                SquadMember.status == MembershipStatus.ACTIVE,
            )
            .exists(),
        )
        return list(await self._session.scalars(stmt))
