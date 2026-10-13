"""Конфигурация приложения из переменных окружения / .env."""

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class BotMode(StrEnum):
    POLLING = "polling"
    WEBHOOK = "webhook"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    database_url: str = "postgresql+asyncpg://pfbc:pfbc@localhost:5432/pfbc"
    admin_tg_ids: Annotated[frozenset[int], NoDecode] = Field(default_factory=frozenset)

    bot_mode: BotMode = BotMode.POLLING
    webhook_base_url: str = ""
    webhook_path_secret: str = "change-me"
    webhook_secret_token: SecretStr = SecretStr("change-me")
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    default_timezone: str = "Europe/Moscow"
    mtgame_base_url: str = "https://mtgame.ru"  # матчи ABL (ФТ-4)
    log_level: str = "INFO"

    @field_validator("admin_tg_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(
                int(part) for part in value.replace(";", ",").split(",") if part.strip()
            )
        if isinstance(value, int):
            return frozenset({value})
        return value

    @property
    def webhook_path(self) -> str:
        return f"/tg/webhook/{self.webhook_path_secret}"

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # значения берутся из окружения
