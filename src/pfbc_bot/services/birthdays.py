"""Дни рождения (ФТ-6): поздравление в группе в сам день и предупреждения тренеру и капитану.

- За 7, 3 и 1 день (настраивается) тренер и капитан состава получают в личку сводку о
  предстоящих днях рождения игроков — чтобы успеть подготовить поздравление.
- В сам день рождения — только сообщение в топик «Поздравления».

Отметка «отправлено» фиксируется до отправки, отдельно для каждого сообщения: сбой посередине
не приводит к повторным поздравлениям на следующем тике.
"""

import html
import random
from datetime import datetime, timedelta

from pfbc_bot.domain.dates import (
    WEEKDAY_SHORT,
    birthday_offsets,
    is_birthday,
    local_to_utc,
    to_local,
    utcnow,
)
from pfbc_bot.domain.enums import STAFF_ROLES, TopicPurpose, UserStatus
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.services.common import send_direct, send_to_squad, to_dto
from pfbc_bot.services.ports import Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru


class BirthdayService:
    def __init__(
        self, uow_factory: UowFactory, notifier: Notifier, *, rng: random.Random | None = None
    ) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier
        self._rng = rng or random.Random()

    async def send_due(self, *, now: datetime | None = None) -> int:
        """Поздравляет именинников во всех их составах начиная с birthday_time (время состава)."""
        now = now or utcnow()
        sent = 0
        async with self._uow_factory() as uow:
            templates = await uow.system.birthday_templates() or list(ru.BIRTHDAY_DEFAULT_TEMPLATES)
            squad_ids = [s.id for s in await uow.squads.list_active()]

        for squad_id in squad_ids:
            async with self._uow_factory() as uow:
                squad = await uow.squads.get(squad_id)
                if squad is None:
                    continue
                settings = await load_squad_settings(uow, squad_id)
                today = to_local(now, squad.timezone).date()
                if not settings.features.birthdays or now < local_to_utc(
                    today, settings.birthday_time, squad.timezone
                ):
                    continue
                celebrants = [
                    (user.id, to_dto(user).mention_html)
                    for _, user in await uow.members.list_active_for_squad(squad_id)
                    if user.status == UserStatus.ACTIVE
                    and user.birth_date is not None
                    and is_birthday(user.birth_date, today)
                ]
            for user_id, mention in celebrants:
                async with self._uow_factory() as uow:
                    key = f"birthday:{user_id}:{squad_id}:{today.year}"
                    if not await uow.system.claim_reminder(key, "birthday"):
                        continue
                    await uow.commit()
                    text = self._rng.choice(templates).format(name=mention)
                    await send_to_squad(uow, self._notifier, squad_id, TopicPurpose.GREETINGS, text)
                    sent += 1
        return sent

    async def send_staff_reminders(self, *, now: datetime | None = None) -> int:
        """Сводки тренеру и капитану о ДР игроков через N дней. Возвращает число учтённых ДР."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            squad_ids = [s.id for s in await uow.squads.list_active()]

        total = 0
        for squad_id in squad_ids:
            async with self._uow_factory() as uow:
                squad = await uow.squads.get(squad_id)
                if squad is None:
                    continue
                settings = await load_squad_settings(uow, squad_id)
                today = to_local(now, squad.timezone).date()
                if not settings.features.birthday_staff_reminders or now < local_to_utc(
                    today, settings.birthday_time, squad.timezone
                ):
                    continue

                upcoming: list[tuple[int, str]] = []  # (user_id, строка сводки)
                for _, user in await uow.members.list_active_for_squad(squad_id):
                    if user.status != UserStatus.ACTIVE or user.birth_date is None:
                        continue
                    for days in birthday_offsets(
                        user.birth_date, today, settings.birthday_staff_reminder_days
                    ):
                        target = today + timedelta(days=days)
                        key = f"bdstaff:{squad_id}:{user.id}:{target.isoformat()}:{days}"
                        if not await uow.system.claim_reminder(key, "birthday_staff"):
                            continue
                        when = ru.BIRTHDAY_IN_DAYS.get(days) or ru.BIRTHDAY_IN_N_DAYS.format(
                            days=days
                        )
                        line = ru.BIRTHDAY_STAFF_LINE.format(
                            name=to_dto(user).mention_html,
                            date=f"{WEEKDAY_SHORT[target.weekday()]} {target:%d.%m}",
                            when=when,
                        )
                        upcoming.append((user.id, line))

                await uow.commit()  # отметки — до рассылки
                if upcoming:
                    header = ru.BIRTHDAY_STAFF_HEADER.format(squad=html.escape(squad.name))
                    staff = await uow.members.users_with_roles(squad_id, STAFF_ROLES)
                    for recipient in staff:
                        # о собственном дне рождения не предупреждаем
                        lines = [line for uid, line in upcoming if uid != recipient.id]
                        if lines:
                            await send_direct(
                                self._notifier, recipient, "\n".join([header, *lines])
                            )
                    total += len(upcoming)
                await uow.commit()
        return total
