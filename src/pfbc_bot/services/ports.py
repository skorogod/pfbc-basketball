"""Порты (интерфейсы) внешнего мира, от которых зависят сервисы (принцип инверсии зависимостей).

Сервисы ничего не знают об aiogram: они работают с Notifier, а реализация живёт в
pfbc_bot.integrations.telegram. В тестах подставляется фейк.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class InlineButton:
    text: str
    callback_data: str | None = None
    url: str | None = None


Keyboard = Sequence[Sequence[InlineButton]]


@dataclass(frozen=True, slots=True)
class SentPoll:
    poll_id: str
    message_id: int


class Notifier(Protocol):
    @property
    def bot_username(self) -> str: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        keyboard: Keyboard | None = None,
        reply_to: int | None = None,
    ) -> int | None:
        """Отправляет HTML-сообщение. Возвращает message_id или None, если доставить нельзя."""
        ...

    async def edit_message(
        self, chat_id: int, message_id: int, text: str, *, keyboard: Keyboard | None = None
    ) -> bool:
        """Редактирует сообщение бота. Telegram не присылает уведомлений о правках."""
        ...

    async def send_poll(
        self,
        chat_id: int,
        question: str,
        options: Sequence[str],
        *,
        thread_id: int | None = None,
    ) -> SentPoll | None:
        """Неанонимный опрос с одним вариантом ответа."""
        ...

    async def stop_poll(self, chat_id: int, message_id: int) -> bool:
        """Закрывает опрос бота: голосовать в нём больше нельзя."""
        ...

    async def is_chat_member(self, chat_id: int, tg_user_id: int) -> bool | None:
        """True/False — состоит ли пользователь в чате; None — проверить не удалось."""
        ...
