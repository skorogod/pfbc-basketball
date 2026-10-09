"""Реализация порта Notifier на aiogram: retry с учётом retry_after, фолбэк на общий топик.

Ошибки Telegram (включая сетевые и 5xx) наружу не выходят: метод возвращает «не доставлено».
Иначе исключение откатило бы транзакцию задачи вместе с отметкой «уже отправлено», и следующий
тик повторил бы уже доставленные сообщения.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters

from pfbc_bot.logging import get_logger
from pfbc_bot.services.ports import Keyboard, SentPoll

log = get_logger(__name__)
T = TypeVar("T")

_MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,
}


def to_markup(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    if not keyboard:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=b.text, callback_data=b.callback_data, url=b.url)
                for b in row
            ]
            for row in keyboard
        ]
    )


class TelegramNotifier:
    def __init__(
        self,
        bot: Bot,
        bot_username: str,
        *,
        max_attempts: int = 3,
        max_retry_wait: float = 10.0,
    ) -> None:
        self._bot = bot
        self._bot_username = bot_username
        self._max_attempts = max_attempts
        # дольше не ждём: отправка часто идёт внутри транзакции, и долгое ожидание держало бы
        # её (и блокировки) открытой — лучше «не доставлено» и повтор на следующем тике
        self._max_retry_wait = max_retry_wait

    @property
    def bot_username(self) -> str:
        return self._bot_username

    async def _call(self, action: Callable[[], Awaitable[T]]) -> T:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await action()
            except TelegramRetryAfter as exc:
                if attempt == self._max_attempts or exc.retry_after > self._max_retry_wait:
                    raise
                log.warning("telegram_retry_after", seconds=exc.retry_after, attempt=attempt)
                await asyncio.sleep(exc.retry_after + 0.5)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _with_thread_fallback(
        self, thread_id: int | None, send: Callable[[int | None], Awaitable[T]]
    ) -> T:
        """Если топик удалили/закрыли — отправляем в общий раздел, а не теряем сообщение."""
        try:
            return await self._call(lambda: send(thread_id))
        except TelegramBadRequest as exc:
            if thread_id is None or "thread" not in exc.message.lower():
                raise
            log.warning("telegram_thread_fallback", thread_id=thread_id, error=exc.message)
            return await self._call(lambda: send(None))

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        keyboard: Keyboard | None = None,
        reply_to: int | None = None,
    ) -> int | None:
        markup = to_markup(keyboard)
        reply = (
            ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
            if reply_to
            else None
        )
        try:
            message = await self._with_thread_fallback(
                thread_id,
                lambda tid: self._bot.send_message(
                    chat_id,
                    text,
                    message_thread_id=tid,
                    reply_markup=markup,
                    reply_parameters=reply,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                ),
            )
        except TelegramAPIError as exc:
            log.warning("telegram_send_failed", chat_id=chat_id, error=str(exc))
            return None
        return message.message_id

    async def edit_message(
        self, chat_id: int, message_id: int, text: str, *, keyboard: Keyboard | None = None
    ) -> bool:
        try:
            await self._call(
                lambda: self._bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=to_markup(keyboard),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            )
        except TelegramBadRequest as exc:
            if "not modified" in exc.message.lower():
                return True
            log.info("telegram_edit_failed", chat_id=chat_id, error=exc.message)
            return False
        except TelegramAPIError as exc:
            log.warning("telegram_edit_failed", chat_id=chat_id, error=str(exc))
            return False
        return True

    async def send_poll(
        self,
        chat_id: int,
        question: str,
        options: Sequence[str],
        *,
        thread_id: int | None = None,
    ) -> SentPoll | None:
        try:
            message = await self._with_thread_fallback(
                thread_id,
                lambda tid: self._bot.send_poll(
                    chat_id,
                    question=question[:300],
                    options=list(options),
                    is_anonymous=False,
                    allows_multiple_answers=False,
                    message_thread_id=tid,
                ),
            )
        except TelegramAPIError as exc:
            log.warning("telegram_poll_failed", chat_id=chat_id, error=str(exc))
            return None
        if message.poll is None:  # pragma: no cover - Telegram всегда возвращает poll
            return None
        return SentPoll(poll_id=message.poll.id, message_id=message.message_id)

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        try:
            await self._call(lambda: self._bot.delete_message(chat_id, message_id))
        except TelegramAPIError as exc:
            log.info("telegram_delete_failed", chat_id=chat_id, error=str(exc))
            return False
        return True

    async def stop_poll(self, chat_id: int, message_id: int) -> bool:
        try:
            await self._call(lambda: self._bot.stop_poll(chat_id, message_id))
        except TelegramAPIError as exc:
            log.info("telegram_stop_poll_failed", chat_id=chat_id, error=str(exc))
            return False
        return True

    async def is_chat_member(self, chat_id: int, tg_user_id: int) -> bool | None:
        try:
            member = await self._call(lambda: self._bot.get_chat_member(chat_id, tg_user_id))
        except TelegramAPIError as exc:
            if isinstance(exc, TelegramBadRequest) and "user not found" in exc.message.lower():
                return False
            log.warning("telegram_get_member_failed", chat_id=chat_id, error=str(exc))
            return None
        if member.status == ChatMemberStatus.RESTRICTED:
            return bool(getattr(member, "is_member", True))
        return member.status in _MEMBER_STATUSES
