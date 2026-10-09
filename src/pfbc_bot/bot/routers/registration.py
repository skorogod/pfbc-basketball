"""/start, /help, /invite и анкета регистрации (ФТ-1)."""

import html
from datetime import date
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from pfbc_bot.bot.helpers import ask_squad, callback_message
from pfbc_bot.bot.keyboards.common import Choice, SquadPick, choices_keyboard, skip_keyboard
from pfbc_bot.bot.states import Registration
from pfbc_bot.domain.dates import parse_date
from pfbc_bot.domain.payments import normalize_phone
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.schemas.users import (
    RegistrationData,
    validate_birth_date,
    validate_full_name,
    validate_mtgame_url,
)
from pfbc_bot.services.container import Services
from pfbc_bot.services.permissions import require
from pfbc_bot.texts import ru

router = Router(name="registration")
router.message.filter(F.chat.type == ChatType.PRIVATE)

FLOW = "reg"
INVITE_FLOW = "invite"


def help_text(ctx: UserContext) -> str:
    """Команды по правам пользователя: что доступно — то и показываем."""
    text = ru.HELP_PLAYER
    perms = [p for p in Permission if ctx.can_anywhere(p)]
    if Permission.EVENTS in perms and Permission.MEETINGS in perms:
        perms.remove(Permission.MEETINGS)  # /event_add уже в справке «события»
    lines = [ru.HELP_BY_PERMISSION[p.value] for p in perms]
    if lines:
        text += "\n".join([ru.HELP_FUNCTIONS, *lines])
    if ctx.is_admin:
        text += ru.HELP_ADMIN
    return text


@router.message(CommandStart())
async def cmd_start(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    await services.registration.mark_dm_available(ctx.user.tg_user_id)
    await state.clear()
    if ctx.user.is_registered:
        await message.answer(f"{ru.REG_ALREADY}\n\n{help_text(ctx)}")
        return
    await message.answer(ru.CLUB_INFO)
    await state.set_state(Registration.full_name)
    await message.answer(ru.REG_ASK_NAME)


@router.message(Command("help"))
async def cmd_help(message: Message, ctx: UserContext) -> None:
    await message.answer(help_text(ctx))


@router.message(Command("invite"))
async def cmd_invite(message: Message, ctx: UserContext, services: Services) -> None:
    if squad := await ask_squad(message, ctx, services, INVITE_FLOW, Permission.INVITE):
        await _send_invite(message, squad, services)


@router.callback_query(SquadPick.filter(F.flow == INVITE_FLOW))
async def on_invite_squad(
    callback: CallbackQuery, callback_data: SquadPick, ctx: UserContext, services: Services
) -> None:
    require(ctx, callback_data.squad_id, Permission.INVITE)
    await callback.answer()
    if message := callback_message(callback):
        await _send_invite(message, await services.squads.get(callback_data.squad_id), services)


async def _send_invite(message: Message, squad: SquadDTO, services: Services) -> None:
    link = services.registration.deep_link(squad.id)
    await message.answer(ru.INVITE.format(squad=html.escape(squad.name), link=link))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(ru.INPUT_CANCELLED)


@router.message(Registration.full_name, F.text)
async def reg_full_name(message: Message, state: FSMContext) -> None:
    try:
        full_name = validate_full_name(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(full_name=full_name)
    await state.set_state(Registration.birth_date)
    await message.answer(ru.REG_ASK_BIRTH, reply_markup=skip_keyboard(FLOW))


@router.message(Registration.birth_date, F.text)
async def reg_birth_date(message: Message, state: FSMContext) -> None:
    try:
        birth = validate_birth_date(parse_date(message.text or ""), today=date.today())
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(birth_date=birth.isoformat())
    await _ask_mtgame(message, state)


@router.message(Registration.mtgame_url, F.text)
async def reg_mtgame(
    message: Message, state: FSMContext, ctx: UserContext, services: Services
) -> None:
    try:
        url = validate_mtgame_url(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(mtgame_url=url)
    await _ask_phone(message, state)


@router.callback_query(
    Registration.birth_date, Choice.filter((F.flow == FLOW) & (F.value == "skip"))
)
async def reg_skip_birth(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await _ask_mtgame(message, state)


@router.callback_query(
    Registration.mtgame_url, Choice.filter((F.flow == FLOW) & (F.value == "skip"))
)
async def reg_skip_mtgame(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await _ask_phone(message, state)


async def _ask_phone(message: Message, state: FSMContext) -> None:
    await state.set_state(Registration.phone)
    await message.answer(ru.REG_ASK_PHONE, reply_markup=skip_keyboard(FLOW))


@router.message(Registration.phone, F.text)
async def reg_phone(
    message: Message, state: FSMContext, ctx: UserContext, services: Services
) -> None:
    try:
        phone = normalize_phone(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(phone=phone)
    await _ask_consent(message, state, ctx, services)


@router.callback_query(Registration.phone, Choice.filter((F.flow == FLOW) & (F.value == "skip")))
async def reg_skip_phone(
    callback: CallbackQuery, state: FSMContext, ctx: UserContext, services: Services
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await _ask_consent(message, state, ctx, services)


@router.callback_query(Registration.consent, Choice.filter(F.flow == FLOW))
async def reg_consent(
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
    if callback_data.value not in {"consent", "guardian"}:
        await state.clear()
        await message.answer(ru.REG_DECLINED)
        return
    data: dict[str, Any] = await state.get_data()
    try:
        registration = RegistrationData(
            full_name=data["full_name"],
            birth_date=data.get("birth_date"),
            mtgame_url=data.get("mtgame_url"),
            phone=data.get("phone"),
            pd_consent=True,
            guardian_consent=callback_data.value == "guardian",
        )
    except (ValidationError, KeyError):
        await state.set_state(Registration.full_name)
        await message.answer(ru.REG_ASK_NAME)
        return
    user = await services.registration.complete(ctx.user.id, registration)
    await state.clear()
    await message.edit_reply_markup(reply_markup=None)
    await message.answer(ru.REG_DONE.format(name=html.escape(user.display_name)))


async def _ask_mtgame(message: Message, state: FSMContext) -> None:
    await state.set_state(Registration.mtgame_url)
    await message.answer(ru.REG_ASK_MTGAME, reply_markup=skip_keyboard(FLOW))


async def _ask_consent(
    message: Message, state: FSMContext, ctx: UserContext, services: Services
) -> None:
    await state.set_state(Registration.consent)
    if await services.registration.requires_guardian_consent(ctx.user.id):
        text = ru.REG_ASK_CONSENT_YOUTH
        options = [("guardian", ru.BTN_CONSENT_GUARDIAN), ("decline", ru.BTN_DECLINE)]
    else:
        text = ru.REG_ASK_CONSENT
        options = [("consent", ru.BTN_CONSENT), ("decline", ru.BTN_DECLINE)]
    await message.answer(text, reply_markup=choices_keyboard(FLOW, options, width=1))
