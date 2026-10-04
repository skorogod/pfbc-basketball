"""Все ORM-модели. Импорт отсюда регистрирует таблицы в Base.metadata (нужно Alembic)."""

from pfbc_bot.db.models.events import Event
from pfbc_bot.db.models.greetings import Greeting
from pfbc_bot.db.models.matches import Match
from pfbc_bot.db.models.payments import PaymentPeriod, PaymentReceipt, PaymentReminderConfig
from pfbc_bot.db.models.polls import Poll, PollAnswer
from pfbc_bot.db.models.squads import (
    Squad,
    SquadChat,
    SquadMember,
    SquadRoleAssignment,
    SquadTopic,
)
from pfbc_bot.db.models.system import AuditLog, FsmState, ReminderLog, SettingsEntry
from pfbc_bot.db.models.trainings import DutyAssignment, Training, TrainingSchedule
from pfbc_bot.db.models.users import User

__all__ = [
    "AuditLog",
    "DutyAssignment",
    "Event",
    "FsmState",
    "Greeting",
    "Match",
    "PaymentPeriod",
    "PaymentReceipt",
    "PaymentReminderConfig",
    "Poll",
    "PollAnswer",
    "ReminderLog",
    "SettingsEntry",
    "Squad",
    "SquadChat",
    "SquadMember",
    "SquadRoleAssignment",
    "SquadTopic",
    "Training",
    "TrainingSchedule",
    "User",
]
