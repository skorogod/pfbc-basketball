"""Настройки: значения по умолчанию в коде, переопределения — в таблице settings."""

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.settings import GlobalSettings, SquadSettings
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.errors import DomainError, PermissionDenied
from pfbc_bot.texts import ru

GLOBAL_SCOPE = "global"


def squad_scope(squad_id: int) -> str:
    return f"squad:{squad_id}"


async def load_squad_settings(uow: UnitOfWork, squad_id: int) -> SquadSettings:
    return SquadSettings.model_validate(await uow.system.get_settings(squad_scope(squad_id)))


async def load_global_settings(uow: UnitOfWork) -> GlobalSettings:
    return GlobalSettings.model_validate(await uow.system.get_settings(GLOBAL_SCOPE))


def _parse_raw(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def has_setting(model_cls: type[BaseModel], dotted_key: str) -> bool:
    """Есть ли настройка по пути «a» или «a.b» (с учётом вложенных моделей)."""
    current: type[BaseModel] | None = model_cls
    for part in dotted_key.split("."):
        if current is None or part not in current.model_fields:
            return False
        annotation = current.model_fields[part].annotation
        is_model = isinstance(annotation, type) and issubclass(annotation, BaseModel)
        current = annotation if is_model else None
    return current is None  # путь должен указывать на значение, а не на целую группу


def apply_override(
    model_cls: type[BaseModel], overrides: dict[str, Any], dotted_key: str, raw: str
) -> dict[str, Any]:
    """Применяет «ключ значение» к переопределениям и валидирует итог. Ключ: a или a.b."""
    if not has_setting(model_cls, dotted_key):
        raise DomainError(f"Неизвестная настройка: {dotted_key}")
    updated = json.loads(json.dumps(overrides))  # глубокая копия
    *path, leaf = dotted_key.split(".")
    node = updated
    for part in path:
        node = node.setdefault(part, {})
    node[leaf] = _parse_raw(raw)
    try:
        model_cls.model_validate(updated)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first["loc"])
        raise DomainError(f"Некорректная настройка {location}: {first['msg']}") from exc
    result: dict[str, Any] = updated
    return result


class SettingsService:
    def __init__(self, uow_factory: UowFactory) -> None:
        self._uow_factory = uow_factory

    @staticmethod
    def is_global_key(key: str) -> bool:
        return has_setting(GlobalSettings, key)

    @staticmethod
    def is_squad_key(key: str) -> bool:
        return has_setting(SquadSettings, key)

    @staticmethod
    def _require_admin(actor: UserContext) -> None:
        """Просмотр и изменение настроек — только админ (сервисы читают их сами)."""
        if not actor.is_admin:
            raise PermissionDenied(ru.NO_PERMISSION)

    async def get_squad(self, actor: UserContext, squad_id: int) -> SquadSettings:
        self._require_admin(actor)
        async with self._uow_factory() as uow:
            return await load_squad_settings(uow, squad_id)

    async def get_global(self, actor: UserContext) -> GlobalSettings:
        self._require_admin(actor)
        async with self._uow_factory() as uow:
            return await load_global_settings(uow)

    async def set_squad_value(
        self, actor: UserContext, squad_id: int, key: str, raw_value: str
    ) -> SquadSettings:
        self._require_admin(actor)
        return SquadSettings.model_validate(
            await self._set(squad_scope(squad_id), SquadSettings, key, raw_value, actor.user.id)
        )

    async def set_global_value(
        self, actor: UserContext, key: str, raw_value: str
    ) -> GlobalSettings:
        self._require_admin(actor)
        return GlobalSettings.model_validate(
            await self._set(GLOBAL_SCOPE, GlobalSettings, key, raw_value, actor.user.id)
        )

    async def _set(
        self,
        scope: str,
        model_cls: type[BaseModel],
        key: str,
        raw_value: str,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            current = await uow.system.get_settings(scope)
            updated = apply_override(model_cls, current, key, raw_value)
            # сохраняем в нормализованном виде (например, время «9:30» → «09:30:00»)
            normalized = model_cls.model_validate(updated).model_dump(
                mode="json", include=set(updated)
            )
            await uow.system.save_settings(scope, normalized)
            await uow.system.audit(
                actor_user_id=actor_user_id,
                action="settings.set",
                entity="settings",
                entity_id=scope,
                payload={"key": key, "value": raw_value},
            )
            await uow.commit()
            return normalized
