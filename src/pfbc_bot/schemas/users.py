import html
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from pfbc_bot.domain.dates import parse_date
from pfbc_bot.domain.enums import UserStatus
from pfbc_bot.domain.payments import normalize_phone
from pfbc_bot.schemas.base import DTO

MTGAME_DOMAIN = "mtgame.ru"


class TelegramUser(BaseModel):
    """Минимум данных о пользователе Telegram, нужный сервисам (без зависимости от aiogram)."""

    tg_user_id: int
    username: str | None = None
    first_name: str | None = None
    is_bot: bool = False


class UserDTO(DTO):
    id: int
    tg_user_id: int
    username: str | None
    first_name: str | None
    full_name: str | None
    birth_date: date | None
    mtgame_url: str | None
    phone: str | None = None
    payment_details: dict[str, Any] | None = None
    pd_consent_at: datetime | None
    is_admin: bool
    dm_available: bool
    status: UserStatus

    @property
    def display_name(self) -> str:
        return (
            self.full_name
            or self.first_name
            or (self.username and f"@{self.username}")
            or (f"id{self.tg_user_id}")
        )

    @property
    def mention_html(self) -> str:
        """Упоминание: в группе человек получает уведомление (дежурные, поздравления)."""
        return f'<a href="tg://user?id={self.tg_user_id}">{html.escape(self.display_name)}</a>'

    @property
    def profile_html(self) -> str:
        """Ссылка на профиль для списков в личке: нажал — и можно написать человеку.
        По username (t.me) открывается всегда; по id — если человек не запретил это
        в настройках приватности Telegram."""
        return profile_link(self.tg_user_id, self.username, self.display_name)

    @property
    def is_registered(self) -> bool:
        return self.pd_consent_at is not None and self.full_name is not None


def validate_full_name(value: str) -> str:
    value = " ".join(value.split())
    if not 2 <= len(value) <= 200:
        raise ValueError("Имя должно быть от 2 до 200 символов")
    if not any(ch.isalpha() for ch in value):
        raise ValueError("Имя должно содержать буквы")
    return value


def validate_birth_date(value: date, *, today: date | None = None) -> date:
    today = today or date.today()
    if value > today:
        raise ValueError("Дата рождения не может быть в будущем")
    if value.year < 1930:
        raise ValueError("Проверьте год рождения")
    return value


def validate_mtgame_url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        host == MTGAME_DOMAIN or host.endswith(f".{MTGAME_DOMAIN}")
    ):
        raise ValueError("Нужна ссылка на профиль с сайта mtgame.ru")
    if not parsed.path.strip("/"):
        raise ValueError("Нужна ссылка именно на профиль игрока, а не на главную страницу")
    return value


class RegistrationData(BaseModel):
    """Анкета регистрации (ФТ-1). Валидация полей — здесь, а не в хэндлерах."""

    full_name: str
    birth_date: date | None = None
    mtgame_url: str | None = None
    phone: str | None = None
    pd_consent: bool = Field(description="Согласие на обработку персональных данных")
    guardian_consent: bool = False

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, value: str) -> str:
        return validate_full_name(value)

    @field_validator("birth_date", mode="before")
    @classmethod
    def _birth_date_parse(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return date.fromisoformat(value)  # формат хранения в FSM
        except ValueError:
            return parse_date(value)  # ввод пользователя: дд.мм.гггг

    @field_validator("birth_date")
    @classmethod
    def _birth_date(cls, value: date | None) -> date | None:
        return validate_birth_date(value) if value else None

    @field_validator("mtgame_url")
    @classmethod
    def _mtgame(cls, value: str | None) -> str | None:
        return validate_mtgame_url(value) if value else None

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        return normalize_phone(value) if value else None

    @field_validator("pd_consent")
    @classmethod
    def _consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Без согласия на обработку персональных данных регистрация невозможна")
        return value


def profile_link(tg_user_id: int, username: str | None, name: str) -> str:
    href = f"https://t.me/{username}" if username else f"tg://user?id={tg_user_id}"
    return f'<a href="{href}">{html.escape(name)}</a>'
