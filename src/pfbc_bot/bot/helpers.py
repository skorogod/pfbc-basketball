"""Общие помощники хэндлеров."""

from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from pfbc_bot.bot.keyboards.common import squads_keyboard
from pfbc_bot.domain.enums import SquadRole
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.texts import ru


async def available_squads(
    ctx: UserContext, services: Services, *roles: SquadRole
) -> list[SquadDTO]:
    """Составы, где у пользователя есть одна из ролей. Админу — все активные составы."""
    if ctx.is_admin:
        return [o.squad for o in await services.squads.overview()]
    return await services.squads.get_many(ctx.squads_with_roles(*roles))


async def ask_squad(
    message: Message, ctx: UserContext, services: Services, flow: str, *roles: SquadRole
) -> SquadDTO | None:
    """Если доступен один состав — возвращает его; иначе показывает выбор и возвращает None."""
    squads = await available_squads(ctx, services, *roles)
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
