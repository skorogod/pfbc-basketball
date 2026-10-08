from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pfbc_bot.db.models import AuditLog, Greeting, ReminderLog, SettingsEntry
from pfbc_bot.domain.enums import GreetingKind


class SystemRepository:
    """Журнал напоминаний (идемпотентность), настройки, аудит, шаблоны поздравлений."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_reminder(self, key: str, kind: str) -> bool:
        """Атомарно «занимает» ключ. False — уже отправлялось.

        Ключ фиксируется вместе с транзакцией: если отправка упала и транзакция откатилась,
        следующий тик повторит попытку.
        """
        stmt = (
            insert(ReminderLog)
            .values(key=key, kind=kind)
            .on_conflict_do_nothing(index_elements=[ReminderLog.key])
            .returning(ReminderLog.id)
        )
        return (await self._session.scalar(stmt)) is not None

    async def release_reminder(self, key: str) -> None:
        """Снимает отметку: событие изменилось (перенос) или отправка не удалась — повторить."""
        await self._session.execute(delete(ReminderLog).where(ReminderLog.key == key))

    async def count_reminders(self, key_prefix: str) -> int:
        stmt = (
            select(func.count())
            .select_from(ReminderLog)
            .where(ReminderLog.key.startswith(key_prefix))
        )
        return int(await self._session.scalar(stmt) or 0)

    async def get_settings(self, scope: str) -> dict[str, Any]:
        entry = await self._session.get(SettingsEntry, scope)
        return dict(entry.value) if entry else {}

    async def save_settings(self, scope: str, value: dict[str, Any]) -> None:
        stmt = insert(SettingsEntry).values(scope=scope, value=value)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SettingsEntry.scope], set_={"value": value}
        )
        await self._session.execute(stmt)

    async def audit(
        self,
        *,
        actor_user_id: int | None,
        action: str,
        entity: str,
        entity_id: object | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                action=action,
                entity=entity,
                entity_id=None if entity_id is None else str(entity_id),
                payload=payload,
            )
        )

    async def birthday_templates(self) -> list[str]:
        stmt = select(Greeting.text).where(Greeting.kind == GreetingKind.BIRTHDAY_TEMPLATE)
        return list(await self._session.scalars(stmt))
