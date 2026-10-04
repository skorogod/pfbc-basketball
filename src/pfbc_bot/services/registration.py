"""Онбординг, регистрация и профиль пользователя (ФТ-1)."""

from dataclasses import dataclass
from datetime import datetime

from pfbc_bot.domain.dates import parse_date, utcnow
from pfbc_bot.domain.enums import UserStatus
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.squads import SquadDTO
from pfbc_bot.schemas.users import (
    RegistrationData,
    UserDTO,
    validate_birth_date,
    validate_full_name,
    validate_mtgame_url,
)
from pfbc_bot.services.common import to_dto
from pfbc_bot.services.errors import NotFound
from pfbc_bot.services.membership import anonymize
from pfbc_bot.services.ports import Notifier

REGISTRATION_START_PREFIX = "reg_"
EDITABLE_FIELDS = ("full_name", "birth_date", "mtgame_url")


@dataclass(frozen=True, slots=True)
class Profile:
    user: UserDTO
    squads: list[SquadDTO]


class RegistrationService:
    def __init__(self, uow_factory: UowFactory, notifier: Notifier) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier

    def deep_link(self, squad_id: int) -> str:
        return f"https://t.me/{self._notifier.bot_username}?start={REGISTRATION_START_PREFIX}{squad_id}"

    async def mark_dm_available(self, tg_user_id: int) -> None:
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_tg_id(tg_user_id)
            if user is not None and not user.dm_available:
                user.dm_available = True
                await uow.commit()

    async def requires_guardian_consent(self, user_id: int) -> bool:
        """Нужно ли согласие законного представителя: пользователь в детском составе."""
        async with self._uow_factory() as uow:
            memberships = await uow.members.list_active_for_user(user_id)
            squads = await uow.squads.list_by_ids([m.squad_id for m in memberships])
            return any(s.is_youth for s in squads)

    async def complete(
        self, user_id: int, data: RegistrationData, *, now: datetime | None = None
    ) -> UserDTO:
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFound("Пользователь не найден")
            user.full_name = data.full_name
            user.birth_date = data.birth_date
            user.mtgame_url = data.mtgame_url
            user.guardian_consent = data.guardian_consent
            user.pd_consent_at = now or utcnow()
            user.dm_available = True
            if user.status == UserStatus.UNREGISTERED:
                user.status = UserStatus.ACTIVE
            await uow.system.audit(
                actor_user_id=user.id, action="user.registered", entity="user", entity_id=user.id
            )
            await uow.commit()
            return to_dto(user)

    async def update_field(self, user_id: int, field: str, raw_value: str | None) -> UserDTO:
        """Изменение одного поля профиля; валидация — те же правила, что при регистрации."""
        if field not in EDITABLE_FIELDS:
            raise ValueError(f"Поле {field} нельзя изменить")
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFound("Пользователь не найден")
            if field == "full_name":
                user.full_name = validate_full_name(raw_value or "")
            elif field == "birth_date":
                user.birth_date = validate_birth_date(parse_date(raw_value)) if raw_value else None
            else:
                user.mtgame_url = validate_mtgame_url(raw_value) if raw_value else None
            await uow.commit()
            return to_dto(user)

    async def delete_personal_data(self, user_id: int) -> None:
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                return
            username, first_name = user.username, user.first_name
            anonymize(user)
            # имя из Telegram — не анкетные данные, нужно для упоминаний
            user.username, user.first_name = username, first_name
            if user.status == UserStatus.ACTIVE:
                user.status = UserStatus.UNREGISTERED
            await uow.system.audit(
                actor_user_id=user.id, action="user.pd_deleted", entity="user", entity_id=user.id
            )
            await uow.commit()

    async def profile(self, user_id: int) -> Profile:
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFound("Пользователь не найден")
            memberships = await uow.members.list_active_for_user(user_id)
            squads = await uow.squads.list_by_ids([m.squad_id for m in memberships])
            return Profile(user=to_dto(user), squads=[SquadDTO.model_validate(s) for s in squads])
