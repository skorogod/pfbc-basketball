"""Типизированные настройки: значения по умолчанию ← global ← squad."""

from datetime import time

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Напоминания об оплате (игры и квитанции): до 10 штук с интервалом до недели
MAX_PAYMENT_REMINDERS = 10
MAX_PAYMENT_REMINDER_INTERVAL_HOURS = 168


class Features(BaseModel):
    """Feature flags — включение и отключение модулей для состава."""

    model_config = ConfigDict(extra="ignore")  # устаревшие ключи в БД не ломают загрузку

    training_polls: bool = True
    duty: bool = True
    birthdays: bool = True
    birthday_staff_reminders: bool = True
    event_reminders: bool = True
    review_offers: bool = True  # после матча — тренеру предложение назначить разбор
    receipt_reminders: bool = True  # ежемесячный опрос «прислали квитанцию тренеру?»


class SquadSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")  # устаревшие ключи в БД не ломают загрузку

    training_poll_time: time = time(9, 0)
    # «ранняя» тренировка — начинается не позже early_training_until (включительно): опрос
    # накануне в training_poll_eve_time, дежурные и сообщения о них — утром в duty_morning_time
    early_training_until: time = time(14, 0)
    training_poll_eve_time: time = time(21, 0)
    duty_morning_time: time = time(8, 0)
    duty_lead_hours: float = Field(default=3.0, gt=0, le=24)
    duty_count: int = Field(default=2, ge=1, le=5)
    duty_newcomer_grace_days: int = Field(default=14, ge=0, le=365)
    duty_arrive_minutes: int = Field(default=15, ge=0, le=120)
    birthday_time: time = time(10, 0)
    # за сколько дней предупреждать тренера и капитана о ДР игрока (в личку)
    birthday_staff_reminder_days: list[int] = Field(default_factory=lambda: [7, 3, 1])
    event_reminder_days: list[int] = Field(default_factory=lambda: [3, 1, 0])
    event_reminder_time: time = time(9, 0)
    # квитанции за тренировки: опрос в этот день месяца и время, срок — через столько часов
    receipt_poll_day: int = Field(default=1, ge=1, le=28)
    receipt_poll_time: time = time(10, 0)
    receipt_deadline_hours: int = Field(default=24, ge=1, le=720)
    # сбор денег за игру: срок по умолчанию (кнопка на шаге «срок» в /game_pay)
    game_payment_deadline_hours: int = Field(default=24, ge=1, le=720)
    # напоминания не оплатившим: первое — в срок, следующие — через интервал; 0 — не напоминать.
    # С 00:00 до 08:00 не отправляются (переносятся на 08:00)
    game_payment_reminder_count: int = Field(default=1, ge=0, le=MAX_PAYMENT_REMINDERS)
    game_payment_reminder_interval_hours: int = Field(
        default=24, ge=1, le=MAX_PAYMENT_REMINDER_INTERVAL_HOURS
    )
    receipt_reminder_count: int = Field(default=1, ge=0, le=MAX_PAYMENT_REMINDERS)
    receipt_reminder_interval_hours: int = Field(
        default=24, ge=1, le=MAX_PAYMENT_REMINDER_INTERVAL_HOURS
    )
    features: Features = Field(default_factory=Features)

    @field_validator("event_reminder_days")
    @classmethod
    def _offsets(cls, value: list[int]) -> list[int]:
        if any(not 0 <= v <= 30 for v in value):
            raise ValueError("Смещения напоминаний — от 0 до 30 дней")
        return sorted(set(value), reverse=True)

    @field_validator("birthday_staff_reminder_days")
    @classmethod
    def _birthday_offsets(cls, value: list[int]) -> list[int]:
        if any(not 1 <= v <= 30 for v in value):
            raise ValueError(
                "Предупреждения о ДР — от 1 до 30 дней (в сам день — поздравление в группе)"
            )
        return sorted(set(value), reverse=True)


class GlobalSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")  # устаревшие ключи в БД не ломают загрузку

    inactive_retention_days: int = Field(default=365, ge=30)
    negative_access_cache_minutes: int = Field(default=10, ge=1)
