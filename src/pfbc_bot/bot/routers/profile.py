"""/profile — просмотр и изменение профиля, /delete_me — удаление ПДн (ФТ-1)."""

import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from pfbc_bot.bot.helpers import callback_message
from pfbc_bot.bot.keyboards.common import Choice, choices_keyboard, skip_keyboard
from pfbc_bot.bot.states import ProfileEdit
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.services.registration import EDITABLE_FIELDS, Profile
from pfbc_bot.texts import ru

router = Router(name="profile")
router.message.filter(F.chat.type == ChatType.PRIVATE)

FLOW = "profile"
DELETE_FLOW = "delme"
FIELD_TITLES = {"full_name": "ФИО", "birth_date": "Дата рождения", "mtgame_url": "mtgame"}
FIELD_PROMPTS = {
    "full_name": ru.REG_ASK_NAME,
    "birth_date": ru.REG_ASK_BIRTH,
    "mtgame_url": ru.REG_ASK_MTGAME,
}


def render_profile(profile: Profile) -> str:
    user = profile.user
    return ru.PROFILE.format(
        full_name=html.escape(user.full_name or ru.NOT_SET),
        birth_date=user.birth_date.strftime("%d.%m.%Y") if user.birth_date else ru.NOT_SET,
        mtgame=html.escape(user.mtgame_url or ru.NOT_SET),
        squads=html.escape(", ".join(s.name for s in profile.squads) or ru.NOT_SET),
    )


@router.message(Command("profile"))
async def cmd_profile(message: Message, ctx: UserContext, services: Services) -> None:
    profile = await services.registration.profile(ctx.user.id)
    await message.answer(
        render_profile(profile),
        reply_markup=choices_keyboard(
            FLOW, [(f, FIELD_TITLES[f]) for f in EDITABLE_FIELDS], width=3
        ),
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
    if callback_data.value not in EDITABLE_FIELDS:
        return
    await state.set_state(ProfileEdit.value)
    await state.update_data(field=callback_data.value)
    keyboard = skip_keyboard(FLOW) if callback_data.value != "full_name" else None
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
    await message.edit_reply_markup(reply_markup=None)
    if callback_data.value != "yes":
        await message.answer(ru.CANCELLED)
        return
    await services.registration.delete_personal_data(ctx.user.id)
    await message.answer(ru.DELETE_ME_DONE)
