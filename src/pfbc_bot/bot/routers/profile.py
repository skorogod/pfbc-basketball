"""/profile — просмотр и изменение профиля, /delete_me — удаление ПДн (ФТ-1)."""

import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from pfbc_bot.bot.helpers import callback_message, dismiss
from pfbc_bot.bot.keyboards.common import Choice, choices_keyboard
from pfbc_bot.bot.routers.game_payments import show_my_details
from pfbc_bot.bot.states import ProfileEdit
from pfbc_bot.domain.enums import SquadRole
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.schemas.payments import parse_details_list
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.services.registration import EDITABLE_FIELDS, Profile
from pfbc_bot.texts import ru

router = Router(name="profile")
router.message.filter(F.chat.type == ChatType.PRIVATE)

FLOW = "profile"
DELETE_FLOW = "delme"
FIELD_TITLES = {
    "full_name": "ФИО",
    "birth_date": "Дата рождения",
    "mtgame_url": "mtgame",
    "phone": "Телефон",
}
FIELD_PROMPTS = {
    "full_name": ru.REG_ASK_NAME,
    "birth_date": ru.REG_ASK_BIRTH,
    "mtgame_url": ru.REG_ASK_MTGAME,
    "phone": ru.REG_ASK_PHONE,
}
PAYMENT = "payment"


def is_coach(ctx: UserContext) -> bool:
    """Реквизиты для сбора денег — у тренеров (по роли) и у тех, кто собирает деньги."""
    return ctx.can_anywhere(Permission.GAME_PAYMENTS) or bool(
        ctx.squads_with_roles(SquadRole.COACH)
    )


def render_profile(profile: Profile, *, with_payment: bool = False) -> str:
    user = profile.user
    payment = ""
    if with_payment:
        saved = parse_details_list(user.payment_details)
        details = "".join(f"\n  {d.text}" for d in saved) or ru.NOT_SET
        payment = ru.PROFILE_PAYMENT.format(details=details)
    return ru.PROFILE.format(
        full_name=html.escape(user.full_name or ru.NOT_SET),
        birth_date=user.birth_date.strftime("%d.%m.%Y") if user.birth_date else ru.NOT_SET,
        mtgame=html.escape(user.mtgame_url or ru.NOT_SET),
        phone=user.phone or ru.NOT_SET,  # слитно — Telegram подсвечивает номер
        squads=html.escape(", ".join(s.name for s in profile.squads) or ru.NOT_SET),
        payment=payment,
    )


@router.message(Command("profile"))
async def cmd_profile(message: Message, ctx: UserContext, services: Services) -> None:
    profile = await services.registration.profile(ctx.user.id)
    buttons = [(f, FIELD_TITLES[f]) for f in EDITABLE_FIELDS]
    if is_coach(ctx):
        buttons.append((PAYMENT, ru.PROFILE_BTN_PAYMENT))
    await message.answer(
        render_profile(profile, with_payment=is_coach(ctx)),
        reply_markup=choices_keyboard(FLOW, buttons, width=2),
    )


@router.callback_query(Choice.filter(F.flow == FLOW))
async def on_edit_field(
    callback: CallbackQuery,
    callback_data: Choice,
    state: FSMContext,
    ctx: UserContext,
    services: Services,
) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    if callback_data.value == "skip":  # «Пропустить» при редактировании — очистить поле
        field = (await state.get_data()).get("field", "")
        await _save(message, state, ctx, services, field, None)
        return
    if callback_data.value == PAYMENT and is_coach(ctx):
        await state.clear()
        await show_my_details(message, ctx, services)
        return
    if callback_data.value not in EDITABLE_FIELDS:
        return
    await state.set_state(ProfileEdit.value)
    await state.update_data(field=callback_data.value)
    # «Очистить», а не «Пропустить»: при редактировании кнопка стирает значение поля
    keyboard = (
        choices_keyboard(FLOW, [("skip", ru.BTN_CLEAR)])
        if callback_data.value != "full_name"
        else None
    )
    await message.answer(FIELD_PROMPTS[callback_data.value], reply_markup=keyboard)


@router.message(ProfileEdit.value, F.text)
async def on_edit_value(
    message: Message, state: FSMContext, ctx: UserContext, services: Services
) -> None:
    field = (await state.get_data()).get("field", "")
    await _save(message, state, ctx, services, field, message.text)


async def _save(
    message: Message,
    state: FSMContext,
    ctx: UserContext,
    services: Services,
    field: str,
    value: str | None,
) -> None:
    if field not in EDITABLE_FIELDS:
        await state.clear()
        return
    try:
        await services.registration.update_field(ctx.user.id, field, value)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(ru.PROFILE_UPDATED)


@router.message(Command("delete_me"))
async def cmd_delete_me(message: Message) -> None:
    await message.answer(
        ru.DELETE_ME_CONFIRM,
        reply_markup=choices_keyboard(DELETE_FLOW, [("yes", ru.BTN_YES), ("no", ru.BTN_NO)]),
    )


@router.callback_query(Choice.filter(F.flow == DELETE_FLOW))
async def on_delete_me(
    callback: CallbackQuery, callback_data: Choice, ctx: UserContext, services: Services
) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    if callback_data.value != "yes":
        await dismiss(message)
        return
    await message.edit_reply_markup(reply_markup=None)
    await services.registration.delete_personal_data(ctx.user.id)
    await message.answer(ru.DELETE_ME_DONE)
