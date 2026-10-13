from decimal import Decimal

import pytest

from pfbc_bot.domain.payments import (
    format_amount,
    normalize_card,
    normalize_phone,
    parse_amount,
)
from pfbc_bot.schemas.payments import PaymentDetails, PaymentMethod


@pytest.mark.parametrize(
    "raw", ["8 (999) 123-45-67", "+7 999 123 45 67", "9991234567", "79991234567"]
)
def test_normalize_phone(raw: str) -> None:
    assert normalize_phone(raw) == "+79991234567"


@pytest.mark.parametrize("raw", ["12345", "телефон", "+7 999"])
def test_bad_phone(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_card_checksum() -> None:
    assert normalize_card("4111 1111 1111 1111") == "4111111111111111"
    with pytest.raises(ValueError, match="опечатка"):
        normalize_card("4111 1111 1111 1112")


def test_amount() -> None:
    assert parse_amount("2 500") == Decimal("2500.00")
    assert parse_amount("2500,5") == Decimal("2500.50")
    assert format_amount(Decimal("2500")) == "2 500 ₽"
    assert format_amount(Decimal("2500.50")) == "2 500,50 ₽"
    for bad in ("0", "-5", "много", "1,005"):
        with pytest.raises(ValueError):
            parse_amount(bad)


def test_payment_details_text() -> None:
    phone = PaymentDetails(method=PaymentMethod.PHONE, value="89991234567", bank=" Сбербанк ")
    assert phone.value == "+79991234567"
    assert phone.text == "📱 По номеру телефона: +79991234567 (Сбербанк)"  # Telegram подсветит
    card = PaymentDetails(method=PaymentMethod.CARD, value="4111111111111111", bank="Т-Банк")
    assert card.text == "💳 Карта: <code>4111 1111 1111 1111</code> (Т-Банк)"
