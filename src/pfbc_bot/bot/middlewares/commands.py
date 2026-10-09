"""Команды в личке: сброс незавершённого диалога и персональное меню команд."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommandScopeChat, CallbackQuery, Message, TelegramObject

from pfbc_bot.bot.commands import commands_for
from pfbc_bot.logging import get_logger
from pfbc_bot.schemas.squads import UserContext

log = get_logger(__name__)
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def _private_chat_id(event: TelegramObject) -> int | None:
    if isinstance(event, Message) and event.chat.type == ChatType.PRIVATE:
        return event.chat.id
    if (
        isinstance(event, CallbackQuery)
        and isinstance(event.message, Message)
        and event.message.chat.type == ChatType.PRIVATE
    ):
        return event.message.chat.id
    return None


class CommandResetMiddleware(BaseMiddleware):
    """Команда посреди диалога — выход из него: иначе шаг диалога принял бы «/event_add»
    за название или дату. /cancel сбрасывает состояние сам (с ответом)."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if (
            isinstance(event, Message)
            and event.chat.type == ChatType.PRIVATE
            and (event.text or "").startswith("/")
        ):
            state: FSMContext | None = data.get("state")
            if state is not None and await state.get_state() is not None:
                await state.clear()
        return await handler(event, data)


class CommandMenuMiddleware(BaseMiddleware):
    """Персональное меню команд по правам. Ставится, только когда набор изменился (кеш в
    памяти процесса): после смены роли или прав меню обновится при следующем обращении."""

    def __init__(self) -> None:
        self._menus: dict[int, tuple[str, ...]] = {}

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        ctx: UserContext | None = data.get("ctx")
        bot: Bot | None = data.get("bot")
        chat_id = _private_chat_id(event)
        if ctx is not None and bot is not None and chat_id is not None:
            await self._sync(bot, chat_id, ctx)
        return await handler(event, data)

    async def _sync(self, bot: Bot, chat_id: int, ctx: UserContext) -> None:
        commands = commands_for(ctx)
        key = tuple(c.command for c in commands)
        if self._menus.get(chat_id) == key:
            return
        try:
            await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=chat_id))
        except TelegramAPIError as exc:  # меню — удобство: сбой не мешает обработке
            log.info("command_menu_failed", chat_id=chat_id, error=str(exc))
            return
        self._menus[chat_id] = key
