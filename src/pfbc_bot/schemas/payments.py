"""Реквизиты для сбора денег и оплата игр (не путать с оплатой тренировок по квитанциям)."""

import html
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from pfbc_bot.domain.payments import format_card, normalize_card, normalize_phone
from pfbc_bot.schemas.base import DTO


class PaymentMethod(StrEnum):
    PHONE = "phone"  # перевод по номеру телефона (СБП)
    CARD = "card"  # перевод на карту


MAX_DETAILS = 5  # реквизитов у одного пользователя


class PaymentDetails(BaseModel):
    """Куда переводить деньги: телефон или карта + банк. id — чтобы изменить или удалить
    конкретные реквизиты из нескольких."""

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    method: PaymentMethod
    value: str
    bank: str = Field(min_length=2, max_length=60)

    def same_as(self, other: "PaymentDetails") -> bool:
        """Те же реквизиты (без учёта id)."""
        return (self.method, self.value, self.bank.lower()) == (
            other.method,
            other.value,
            other.bank.lower(),
        )

    @property
    def short(self) -> str:
        """Для кнопок: «Сбербанк, +79991234567» / «Т-Банк, карта *5678»."""
        if self.method == PaymentMethod.PHONE:
            return f"{self.bank}, {self.value}"[:50]
        return f"{self.bank}, карта *{self.value[-4:]}"[:50]

    @field_validator("bank")
    @classmethod
    def _bank(cls, value: str) -> str:
        return " ".join(value.split())

    @model_validator(mode="after")
    def _value(self) -> Self:
        if self.method == PaymentMethod.PHONE:
            self.value = normalize_phone(self.value)
        else:
            self.value = normalize_card(self.value)
        return self

    @property
    def text(self) -> str:
        """HTML. Телефон — слитно (+79991234567): так Telegram подсвечивает его, по нажатию —
        позвонить или скопировать. Карта — моноширинным, копируется нажатием."""
        bank = html.escape(self.bank)
        if self.method == PaymentMethod.PHONE:
            return f"📱 По номеру телефона: {self.value} ({bank})"
        return f"💳 Карта: <code>{format_card(self.value)}</code> ({bank})"


def parse_details_list(raw: object) -> list[PaymentDetails]:
    """Реквизиты из БД: список (старый формат — один объект). Битые записи пропускаются."""
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    result = []
    for item in items:
        try:
            result.append(PaymentDetails.model_validate(item))
        except ValueError:
            continue
    return result


class GamePaymentCreate(BaseModel):
    match_event_id: int
    collector_user_id: int  # кому переводят деньги
    amount: Decimal = Field(gt=0, lt=1_000_000)
    deadline_at: datetime | None = None
    details: PaymentDetails
    # заявка на игру — кто платит (источник истины для напоминаний и «все оплатили»)
    player_ids: list[int] = Field(min_length=1, max_length=30)
    # действующий сбор по игре заменяется (/game_pay); False — отказ («Начать сбор»: второй
    # тренер не перебивает уже объявленный сбор)
    replace_active: bool = True

    @field_validator("player_ids")
    @classmethod
    def _unique_players(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class GamePaymentDTO(DTO):
    id: int
    squad_id: int
    event_id: int
    amount: Decimal
    deadline_at: datetime | None
    details: PaymentDetails
    collector_user_id: int | None
