"""Телефон и реквизиты для сбора денег: разбор ввода и проверка (чистые функции)."""

import re
from decimal import ROUND_CEILING, Decimal, InvalidOperation

_DIGITS = re.compile(r"\D")


def normalize_phone(text: str) -> str:
    """«8 (999) 123-45-67», «+7 999 1234567», «9991234567» → «+79991234567»."""
    raw = text.strip()
    if not re.fullmatch(r"[\d\s()+\-]+", raw):
        raise ValueError("Телефон — только цифры, например +79991234567")
    digits = _DIGITS.sub("", raw)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10 and not raw.startswith("+"):
        digits = "7" + digits
    if not 11 <= len(digits) <= 15:
        raise ValueError("Проверьте номер телефона, например +79991234567")
    return f"+{digits}"


def normalize_card(text: str) -> str:
    """Номер карты: 16–19 цифр, пробелы и дефисы допускаются; проверка контрольной цифры."""
    raw = text.strip()
    if not re.fullmatch(r"[\d\s\-]+", raw):
        raise ValueError("Номер карты — только цифры, например 2202 2000 1234 5678")
    digits = _DIGITS.sub("", raw)
    if not 16 <= len(digits) <= 19 or not _luhn_ok(digits):
        raise ValueError("Проверьте номер карты — похоже, в нём опечатка")
    return digits


def format_card(card: str) -> str:
    return " ".join(card[i : i + 4] for i in range(0, len(card), 4))


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def parse_amount(text: str) -> Decimal:
    """Сумма в рублях: «2500», «2 500», «2500,50» → Decimal."""
    raw = text.strip().replace(" ", "").replace(" ", "").replace(",", ".")
    raw = raw.removesuffix("₽").removesuffix("р").removesuffix("руб")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Сумма — число рублей, например 2500") from exc
    if not amount.is_finite() or amount <= 0 or amount >= 1_000_000:
        raise ValueError("Сумма — от 1 до 999 999 рублей")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Не больше двух знаков после запятой")
    return amount.quantize(Decimal("0.01"))


def format_amount(amount: Decimal) -> str:
    """2500.00 → «2 500 ₽», 2500.50 → «2 500,50 ₽»."""
    whole = int(amount)
    text = f"{whole:,}".replace(",", " ")
    kopecks = int((amount - whole) * 100)
    return f"{text},{kopecks:02d} ₽" if kopecks else f"{text} ₽"


def per_player_amount(price: Decimal, players: int) -> Decimal:
    """Сумма с игрока: стоимость игры ÷ заявка, вверх до рубля."""
    return (price / players).to_integral_value(rounding=ROUND_CEILING)
