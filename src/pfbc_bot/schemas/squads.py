from datetime import datetime

from pydantic import BaseModel, Field

from pfbc_bot.domain.enums import MembershipStatus, SquadRole, TopicPurpose
from pfbc_bot.schemas.base import DTO
from pfbc_bot.schemas.users import UserDTO


class SquadDTO(DTO):
    id: int
    name: str
    age_group: str | None
    is_youth: bool
    timezone: str
    is_active: bool


class SquadChatDTO(DTO):
    squad_id: int
    chat_id: int
    title: str | None


class MembershipDTO(DTO):
    user_id: int
    squad_id: int
    status: MembershipStatus
    first_joined_at: datetime
    joined_at: datetime
    left_at: datetime | None


class SquadMemberView(BaseModel):
    """Участник состава вместе с ролями — для списков и выбора."""

    user: UserDTO
    membership: MembershipDTO
    roles: frozenset[SquadRole] = frozenset()


class Destination(BaseModel):
    """Куда публиковать сообщение состава: чат + топик (None — общий раздел)."""

    squad_id: int
    chat_id: int
    thread_id: int | None = None


class UserContext(BaseModel):
    """Права пользователя на время обработки апдейта: активные составы и роли в них."""

    user: UserDTO
    is_admin: bool
    roles: dict[int, frozenset[SquadRole]] = Field(default_factory=dict)  # squad_id → роли

    @property
    def squad_ids(self) -> list[int]:
        return sorted(self.roles)

    @property
    def is_member(self) -> bool:
        return bool(self.roles)

    @property
    def is_allowed(self) -> bool:
        return self.is_admin or self.is_member

    def has_role(self, squad_id: int, *roles: SquadRole) -> bool:
        if self.is_admin:
            return True
        return bool(self.roles.get(squad_id, frozenset()) & set(roles))

    def squads_with_roles(self, *roles: SquadRole) -> list[int]:
        """Составы, где у пользователя есть одна из ролей (админ — все свои составы)."""
        return [sid for sid in self.squad_ids if self.has_role(sid, *roles)]


class TopicBinding(BaseModel):
    squad_id: int
    purpose: TopicPurpose
    thread_id: int | None
