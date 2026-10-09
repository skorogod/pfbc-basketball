"""Реквизиты для сбора денег и оплата игр (не путать с оплатой тренировок по квитанциям)."""

import html
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from pfbc_bot.domain.payments import format_card, format_phone, normalize_card, normalize_phone
from pfbc_bot.schemas.base import DTO


class PaymentMethod(StrEnum):
    PHONE = "phone"  # перевод по номеру телефона (СБП)
    CARD = "card"  # перевод на карту


class PaymentDetails(BaseModel):
    """Куда переводить деньги: телефон или карта + банк."""

    method: PaymentMethod
    value: str
    bank: str = Field(min_length=2, max_length=60)

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
        """HTML: номер — моноширинным, чтобы скопировать нажатием."""
        bank = html.escape(self.bank)
        if self.method == PaymentMethod.PHONE:
            return f"📱 По номеру телефона: <code>{format_phone(self.value)}</code> ({bank})"
        return f"💳 Карта: <code>{format_card(self.value)}</code> ({bank})"


class GamePaymentCreate(BaseModel):
    match_event_id: int
    collector_user_id: int  # кому переводят деньги
    amount: Decimal = Field(gt=0, lt=1_000_000)
    deadline_at: datetime | None = None
    details: PaymentDetails


class GamePaymentDTO(DTO):
    id: int
    squad_id: int
    event_id: int
    amount: Decimal
    deadline_at: datetime | None
    details: PaymentDetails
    collector_user_id: int | None
