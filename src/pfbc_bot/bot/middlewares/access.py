"""AccessMiddleware (ФТ-0): первым в цепочке проверяет членство и кладёт права в data['ctx'].

Участника, вступившего в группу до подключения бота, middleware находит: по сообщению в группе
(автор — участник) или по /start в личке (проверка через Telegram; в том числе админа — ему бот
доступен и без членства, но в составы он попадает только как участник группы). Ответы на опросы
проверяет PollService.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as TgUser

from pfbc_bot.logging import get_logger
from pfbc_bot.schemas.users import TelegramUser
from pfbc_bot.services.container import Services
from pfbc_bot.texts import ru

log = get_logger(__name__)
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def to_telegram_user(user: TgUser) -> TelegramUser:
    return TelegramUser(
        tg_user_id=user.id, username=user.username, first_name=user.first_name, is_bot=user.is_bot
    )


def _group_message_chat(event: TelegramObject) -> int | None:
    """ID группы, если это сообщение участника в группе. Служебное «вышел из группы» — не в счёт:
    его автор уже не участник."""
    if (
        isinstance(event, Message)
        and event.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
        and event.left_chat_member is None
    ):
        return event.chat.id
    return None


def _is_private_start(event: TelegramObject) -> bool:
    return (
        isinstance(event, Message)
        and event.chat.type == ChatType.PRIVATE
        and (event.text or "").startswith("/start")
    )


class AccessMiddleware(BaseMiddleware):
    def __init__(self, *, deny_notice_interval: float = 600) -> None:
        self._deny_interval = deny_notice_interval
        self._last_denied: dict[int, float] = {}

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return None
        services: Services = data["services"]
        user = to_telegram_user(tg_user)
        ctx = await services.access.load_context(user)
        # автор сообщения в группе состава — её участник, даже если вступил до подключения бота
        chat_id = _group_message_chat(event)
        if chat_id is not None and await services.membership.on_group_message(
            chat_id, user, ctx.squad_ids if ctx is not None else []
        ):
            ctx = await services.access.load_context(user)
        # пользователь мог вступить в группу до подключения бота — проверяем через Telegram.
        # Админу доступ открыт и без членства, но участником состава он становится только так
        if (
            (ctx is None or not ctx.is_member)
            and _is_private_start(event)
            and await services.membership.verify_via_telegram(user)
        ):
            ctx = await services.access.load_context(user)
        if ctx is None or not ctx.is_allowed:
            await self._deny(event, tg_user.id)
            return None
        data["ctx"] = ctx
        return await handler(event, data)

    async def _deny(self, event: TelegramObject, tg_user_id: int) -> None:
        log.info("access_denied", tg_user_id=tg_user_id, update_type=type(event).__name__)
        if isinstance(event, CallbackQuery):
            await event.answer(ru.ACCESS_DENIED_CALLBACK, show_alert=True)
            return
        if not isinstance(event, Message) or event.chat.type != ChatType.PRIVATE:
            return  # в группах и на опросах — молча игнорируем
        now = time.monotonic()
        if now - self._last_denied.get(tg_user_id, -self._deny_interval) < self._deny_interval:
            return
        self._last_denied[tg_user_id] = now
        await event.answer(ru.ACCESS_DENIED)
