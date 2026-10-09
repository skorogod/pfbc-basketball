"""Меню команд Telegram: у каждого — только то, что ему доступно по правам.

Общее меню (для всех личных чатов) — базовые команды; персональное (BotCommandScopeChat) —
базовые + команды по правам + админские. Обновляется при обращении пользователя к боту.
"""

from aiogram.types import BotCommand

from pfbc_bot.domain.permissions import Permission
from pfbc_bot.schemas.squads import UserContext

BASE_COMMANDS = [
    BotCommand(command="start", description="Начать / регистрация"),
    BotCommand(command="help", description="Список команд"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="events", description="Ближайшие события"),
]
CANCEL = BotCommand(command="cancel", description="Прервать ввод")

COMMANDS_BY_PERMISSION: dict[Permission, list[BotCommand]] = {
    Permission.SCHEDULE: [
        BotCommand(command="schedule", description="Расписание тренировок"),
        BotCommand(command="trainings", description="Ближайшие тренировки: отмена, перенос"),
        BotCommand(command="training_add", description="Разовая тренировка"),
    ],
    Permission.TRAINING_POLL: [BotCommand(command="poll", description="Опрос на тренировку")],
    Permission.ATTENDANCE: [BotCommand(command="attendance", description="Кто придёт")],
    Permission.DUTY: [BotCommand(command="duty", description="Дежурные: замена и выбор")],
    Permission.MEETINGS: [
        BotCommand(command="event_add", description="Добавить событие"),
        BotCommand(command="event_move", description="Перенести событие"),
        BotCommand(command="event_del", description="Удалить событие"),
    ],
    Permission.EVENTS: [
        BotCommand(command="event_add", description="Добавить событие"),
        BotCommand(command="event_move", description="Перенести событие"),
        BotCommand(command="event_del", description="Удалить событие"),
        BotCommand(command="review", description="Назначить разбор игры"),
        BotCommand(command="tournaments", description="Турниры: матчи и стоимость игры"),
    ],
    Permission.GAME_PAYMENTS: [BotCommand(command="game_pay", description="Сбор денег за игру")],
    Permission.INVITE: [BotCommand(command="invite", description="Ссылка для новичка")],
}

ADMIN_COMMANDS = [
    BotCommand(command="squads", description="Составы"),
    BotCommand(command="roles", description="Роли: тренер, капитан"),
    BotCommand(command="admins", description="Админы бота"),
    BotCommand(command="permissions", description="Права ролей"),
    BotCommand(command="settings", description="Настройки"),
]


def commands_for(ctx: UserContext) -> list[BotCommand]:
    """Меню пользователя: базовые команды, затем — по правам (без повторов), админские, отмена."""
    result = list(BASE_COMMANDS)
    seen = {c.command for c in result}
    for permission, commands in COMMANDS_BY_PERMISSION.items():
        if not ctx.can_anywhere(permission):
            continue
        for command in commands:
            if command.command not in seen:
                seen.add(command.command)
                result.append(command)
    if ctx.is_admin:
        result += ADMIN_COMMANDS
    return [*result, CANCEL]
