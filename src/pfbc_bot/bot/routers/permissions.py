"""/permissions — что могут тренер, капитан и игрок (админ может всё). Только для админа.

Роль → список функций кнопками ✅/▫️: нажатие включает или выключает функцию у роли.
Изменения действуют сразу (права читаются на каждом апдейте).
"""

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pfbc_bot.bot.helpers import callback_message
from pfbc_bot.domain.enums import ROLE_TITLES, SquadRole
from pfbc_bot.domain.permissions import PERMISSION_TITLES, Permission, allowed_for
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.texts import ru

router = Router(name="permissions")
router.message.filter(F.chat.type == ChatType.PRIVATE)

ROLE_ORDER = (SquadRole.COACH, SquadRole.CAPTAIN, SquadRole.PLAYER)


class PermAction(CallbackData, prefix="pm"):
    action: str  # role — открыть роль, toggle — переключить право, reset — по умолчанию
    role: str
    perm: str = ""


@router.message(Command("permissions"))
async def cmd_permissions(message: Message, ctx: UserContext, services: Services) -> None:
    await services.permissions.role_permissions(ctx)  # проверка прав: только админ
    builder = InlineKeyboardBuilder()
    for role in ROLE_ORDER:
        builder.button(
            text=ROLE_TITLES[role].capitalize(),
            callback_data=PermAction(action="role", role=role.value),
        )
    builder.adjust(3)
    await message.answer(ru.PERMISSIONS_CHOOSE_ROLE, reply_markup=builder.as_markup())


@router.callback_query(PermAction.filter())
async def on_perm_action(
    callback: CallbackQuery, callback_data: PermAction, ctx: UserContext, services: Services
) -> None:
    role = SquadRole(callback_data.role)
    if callback_data.action == "locked":
        await callback.answer(ru.PERMISSION_LOCKED, show_alert=True)
        return
    if callback_data.action == "toggle":
        perms = await services.permissions.toggle(ctx, role, Permission(callback_data.perm))
    elif callback_data.action == "reset":
        perms = await services.permissions.reset(ctx, role)
    else:
        perms = (await services.permissions.role_permissions(ctx))[role]
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    text, markup = _role_card(role, perms)
    if callback_data.action == "role":
        await message.answer(text, reply_markup=markup)
    else:
        await message.edit_text(text, reply_markup=markup)


def _role_card(role: SquadRole, perms: frozenset[Permission]) -> tuple[str, InlineKeyboardMarkup]:
    builder = InlineKeyboardBuilder()
    for perm in Permission:
        if not allowed_for(role, perm):  # закреплено за другой ролью — не переключается
            builder.button(
                text=f"{ru.PERMISSION_LOCKED_MARK} {PERMISSION_TITLES[perm]}",
                callback_data=PermAction(action="locked", role=role.value, perm=perm.value),
            )
            continue
        mark = "✅" if perm in perms else "▫️"
        builder.button(
            text=f"{mark} {PERMISSION_TITLES[perm]}",
            callback_data=PermAction(action="toggle", role=role.value, perm=perm.value),
        )
    builder.button(
        text=ru.PERMISSIONS_BTN_RESET, callback_data=PermAction(action="reset", role=role.value)
    )
    builder.adjust(1)
    return ru.PERMISSIONS_ROLE.format(role=ROLE_TITLES[role]), builder.as_markup()
