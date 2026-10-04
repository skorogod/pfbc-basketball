"""Ожидаемые ошибки бизнес-логики показываются пользователю, остальные — логируются."""

from aiogram import F, Router
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import CallbackQuery, ErrorEvent, Message

from pfbc_bot.logging import get_logger
from pfbc_bot.services.errors import DomainError

router = Router(name="errors")
log = get_logger(__name__)


@router.error(ExceptionTypeFilter(DomainError), F.update.message.as_("message"))
async def domain_error_message(event: ErrorEvent, message: Message) -> None:
    await message.answer(str(event.exception))


@router.error(ExceptionTypeFilter(DomainError), F.update.callback_query.as_("callback"))
async def domain_error_callback(event: ErrorEvent, callback: CallbackQuery) -> None:
    await callback.answer(str(event.exception), show_alert=True)


@router.error()
async def unexpected_error(event: ErrorEvent) -> None:
    log.error(
        "unhandled_error",
        update_id=event.update.update_id,
        error=repr(event.exception),
        exc_info=event.exception,
    )
