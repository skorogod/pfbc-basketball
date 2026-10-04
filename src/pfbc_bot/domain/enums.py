"""Доменные перечисления. Хранятся в БД строками (не native enum) — проще мигрировать."""

from enum import StrEnum


class UserStatus(StrEnum):
    UNREGISTERED = "unregistered"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ANONYMIZED = "anonymized"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    LEFT = "left"
    REMOVED = "removed"


class SquadRole(StrEnum):
    PLAYER = "player"
    CAPTAIN = "captain"
    COACH = "coach"


STAFF_ROLES: frozenset[SquadRole] = frozenset({SquadRole.CAPTAIN, SquadRole.COACH})


class TopicPurpose(StrEnum):
    GENERAL = "general"
    TRAININGS = "trainings"
    GAMES = "games"
    GREETINGS = "greetings"
    PAYMENTS = "payments"


class TrainingStatus(StrEnum):
    PLANNED = "planned"
    MOVED = "moved"
    CANCELLED = "cancelled"


ACTIVE_TRAINING_STATUSES: frozenset[TrainingStatus] = frozenset(
    {TrainingStatus.PLANNED, TrainingStatus.MOVED}
)


class TrainingSource(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class MatchSource(StrEnum):
    PARSER = "parser"
    MANUAL = "manual"


class PollType(StrEnum):
    TRAINING = "training"
    MATCH = "match"
    REVIEW = "review"
    CUSTOM = "custom"


class DutyStatus(StrEnum):
    ASSIGNED = "assigned"
    REPLACED = "replaced"
    DONE = "done"


COUNTED_DUTY_STATUSES: frozenset[DutyStatus] = frozenset({DutyStatus.ASSIGNED, DutyStatus.DONE})


class GreetingKind(StrEnum):
    BIRTHDAY_TEMPLATE = "birthday_template"
    HOLIDAY = "holiday"


class GreetingStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENT = "sent"
    CANCELLED = "cancelled"


class ReceiptStatus(StrEnum):
    REQUESTED = "requested"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"


# Длительность матча ABL для проверки пересечений (в таблице matches её нет).
MATCH_DURATION_MINUTES = 120


class EventType(StrEnum):
    MATCH = "match"
    CAMP = "camp"
    TOURNAMENT = "tournament"
    MEETING = "meeting"
    OTHER = "other"


EVENT_TYPE_TITLES: dict[EventType, str] = {
    EventType.MATCH: "Матч",
    EventType.CAMP: "Сбор",
    EventType.TOURNAMENT: "Турнир",
    EventType.MEETING: "Собрание",
    EventType.OTHER: "Прочее",
}

ROLE_TITLES: dict[SquadRole, str] = {
    SquadRole.PLAYER: "игрок",
    SquadRole.CAPTAIN: "капитан",
    SquadRole.COACH: "тренер",
}

# Варианты ответа опроса на тренировку; индекс YES используется при выборе дежурных.
TRAINING_POLL_OPTIONS: tuple[str, ...] = ("Да", "Нет", "Опоздаю")
TRAINING_POLL_YES = 0
