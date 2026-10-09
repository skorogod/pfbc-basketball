"""Общие помощники хэндлеров."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from pfbc_bot.bot.keyboards.common import squads_keyboard
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.texts import ru


async def available_squads(
    ctx: UserContext, services: Services, *permissions: Permission
) -> list[SquadDTO]:
    """Составы, где у пользователя есть хотя бы одно из прав (без прав — все его составы).
    Админу — все активные составы."""
    if ctx.is_admin:
        return [o.squad for o in await services.squads.overview()]
    squad_ids = [
        sid for sid in ctx.squad_ids if not permissions or any(ctx.can(sid, p) for p in permissions)
    ]
    return await services.squads.get_many(squad_ids)


async def ask_squad(
    message: Message,
    ctx: UserContext,
    services: Services,
    flow: str,
    *permissions: Permission,
) -> SquadDTO | None:
    """Если доступен один состав — возвращает его; иначе показывает выбор и возвращает None."""
    squads = await available_squads(ctx, services, *permissions)
    if not squads:
        await message.answer(ru.NO_SQUADS_FOR_ACTION)
        return None
    if len(squads) == 1:
        return squads[0]
    await message.answer(ru.CHOOSE_SQUAD, reply_markup=squads_keyboard(squads, flow))
    return None


def callback_message(callback: CallbackQuery) -> Message | None:
    """Сообщение, к которому привязана кнопка (если оно ещё доступно)."""
    msg = callback.message
    return msg if msg is not None and not isinstance(msg, InaccessibleMessage) else None


async def dismiss(message: Message) -> None:
    """«Отмена» в вопросе: вопрос просто убирается, без сообщения «Отменено» — его легко
    принять за отмену самого события. Старше 48 часов Telegram удалить не даст — снимаем кнопки."""
    try:
        await message.delete()
    except TelegramBadRequest:
        await message.edit_reply_markup(reply_markup=None)
