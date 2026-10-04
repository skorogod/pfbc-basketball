from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, CreatedAtMixin, IdMixin


class ReminderLog(IdMixin, Base):
    """Журнал отправленных автоматических сообщений — гарантирует идемпотентность задач."""

    __tablename__ = "reminder_log"

    key: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    sent_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SettingsEntry(Base):
    """Переопределения настроек: scope = 'global' или 'squad:<id>'. Включает feature flags."""

    __tablename__ = "settings"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]]


class AuditLog(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_log"

    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any] | None]


class FsmState(Base):
    """Хранилище FSM aiogram в PostgreSQL."""

    __tablename__ = "fsm_states"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[dict[str, Any]]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
