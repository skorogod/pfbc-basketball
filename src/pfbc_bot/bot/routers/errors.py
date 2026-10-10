"""Ожидаемые ошибки бизнес-логики показываются пользователю, остальные — логируются."""

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import CallbackQuery, ErrorEvent, Message

from pfbc_bot.logging import get_logger
from pfbc_bot.services.errors import DomainError
from pfbc_bot.texts import ru

router = Router(name="errors")
log = get_logger(__name__)


@router.error(ExceptionTypeFilter(DomainError), F.update.message.as_("message"))
async def domain_error_message(event: ErrorEvent, message: Message) -> None:
    await message.answer(str(event.exception))


@router.error(ExceptionTypeFilter(DomainError), F.update.callback_query.as_("callback"))
async def domain_error_callback(event: ErrorEvent, callback: CallbackQuery) -> None:
    """Всплывающее окно. Но на нажатие Telegram принимает один ответ: если обработчик уже
    ответил (чтобы кнопка не «крутилась»), — текст приходит в личку обычным сообщением."""
    text = str(event.exception)
    try:
        await callback.answer(text, show_alert=True)
    except TelegramBadRequest:
        if callback.bot is not None:
            await callback.bot.send_message(callback.from_user.id, text)


@router.error()
async def unexpected_error(event: ErrorEvent) -> None:
    """Непредвиденный сбой: в лог — подробности, человеку — понятный ответ, а не тишина
    и «крутящаяся» кнопка."""
    log.error(
        "unhandled_error",
        update_id=event.update.update_id,
        error=repr(event.exception),
        exc_info=event.exception,
    )
    try:
        if event.update.callback_query is not None:
            await event.update.callback_query.answer(ru.UNEXPECTED_ERROR, show_alert=True)
        elif event.update.message is not None and event.update.message.chat.type == "private":
            await event.update.message.answer(ru.UNEXPECTED_ERROR)
    except TelegramAPIError:  # ответ — удобство; сбой ответа не должен порождать новый
        log.info("unexpected_error_reply_failed", update_id=event.update.update_id)
