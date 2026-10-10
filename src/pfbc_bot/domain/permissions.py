"""Права: что может делать роль в составе. Значения по умолчанию — здесь, изменения админа —
в таблице settings (scope «permissions»). Админ может всё всегда; только админу — управление
составами, ролями, админами, настройками и правами (в таблицу не входят)."""

from enum import StrEnum

from pfbc_bot.domain.enums import SquadRole


class Permission(StrEnum):
    SCHEDULE = "schedule"  # расписание, разовые тренировки, отмена и перенос
    TRAINING_POLL = "training_poll"  # опрос на тренировку вручную
    ATTENDANCE = "attendance"  # сводки ответов (и оплаты игр)
    DUTY = "duty"  # дежурства: замена, ручной выбор, исправление
    MEETINGS = "meetings"  # события «Собрание»
    # остальные события: матчи, турниры (стоимость, привязка матчей), разборы, сборы, прочее
    EVENTS = "events"
    GAME_PAYMENTS = "game_payments"  # сбор денег за игру и сводка по оплате (только тренер)
    INVITE = "invite"  # ссылка на регистрацию
    RECEIPTS = "receipts"  # напоминание прислать тренеру квитанцию за тренировки


PERMISSION_TITLES: dict[Permission, str] = {
    Permission.SCHEDULE: "Расписание и тренировки",
    Permission.TRAINING_POLL: "Опрос на тренировку вручную",
    Permission.ATTENDANCE: "Сводки: кто придёт",
    Permission.DUTY: "Дежурства по залу",
    Permission.MEETINGS: "Собрания команды",
    Permission.EVENTS: "Игры, турниры, разборы и прочие события",
    Permission.GAME_PAYMENTS: "Сбор денег за игру (только тренер)",
    Permission.INVITE: "Приглашение новичков (/invite)",
    Permission.RECEIPTS: "Напоминание о квитанциях за тренировки",
}

DEFAULT_ROLE_PERMISSIONS: dict[SquadRole, frozenset[Permission]] = {
    SquadRole.PLAYER: frozenset({Permission.ATTENDANCE}),
    SquadRole.CAPTAIN: frozenset(
        {
            Permission.TRAINING_POLL,
            Permission.ATTENDANCE,
            Permission.DUTY,
            Permission.MEETINGS,
            Permission.INVITE,
        }
    ),
    SquadRole.COACH: frozenset(
        {
            Permission.SCHEDULE,
            Permission.TRAINING_POLL,
            Permission.ATTENDANCE,
            Permission.MEETINGS,
            Permission.EVENTS,
            Permission.GAME_PAYMENTS,
            Permission.INVITE,
            Permission.RECEIPTS,
        }
    ),
}

# Закреплённые права: доступны только этим ролям — ни по умолчанию, ни через /permissions
# другим ролям их не выдать (админ может всё).
PERMISSION_ROLES: dict[Permission, frozenset[SquadRole]] = {
    Permission.GAME_PAYMENTS: frozenset({SquadRole.COACH}),
}


def allowed_for(role: SquadRole, permission: Permission) -> bool:
    """Можно ли выдать право роли (закреплённые — только своим ролям)."""
    roles = PERMISSION_ROLES.get(permission)
    return roles is None or role in roles


def merge_role_permissions(
    overrides: dict[str, object],
) -> dict[SquadRole, frozenset[Permission]]:
    """Права ролей: по умолчанию ← сохранённые админом. Неизвестные роли и коды пропускаются
    (например, право убрали из кода, а в БД оно осталось)."""
    known = {p.value for p in Permission}
    result = dict(DEFAULT_ROLE_PERMISSIONS)
    for role in SquadRole:
        saved = overrides.get(role.value)
        if isinstance(saved, list):
            result[role] = frozenset(Permission(p) for p in saved if p in known)
    # закреплённое право чужой роли не действует, даже если сохранено в БД
    return {
        role: frozenset(p for p in perms if allowed_for(role, p)) for role, perms in result.items()
    }


def permissions_for(
    roles: frozenset[SquadRole], table: dict[SquadRole, frozenset[Permission]]
) -> frozenset[Permission]:
    """Права пользователя в составе — объединение прав его ролей (игрок — всегда)."""
    perms: frozenset[Permission] = table.get(SquadRole.PLAYER, frozenset())
    for role in roles:
        perms |= table.get(role, frozenset())
    return perms
