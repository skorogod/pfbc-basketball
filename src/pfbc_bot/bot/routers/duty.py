"""Дежурства в личке бота (ФТ-3): /duty, замена, ручной выбор, отказ, исправление прошедшего
дежурства — всё с подтверждением.

Права проверяет сервис; здесь — только показ вопросов и результатов.
"""

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from pfbc_bot import callbacks
from pfbc_bot.bot.helpers import ask_squad, callback_message
from pfbc_bot.bot.keyboards.common import SquadPick
from pfbc_bot.integrations.telegram.notifier import to_markup
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.services.duty import DUTY_MANAGERS, Prompt
from pfbc_bot.texts import ru

router = Router(name="duty")
DUTY_FLOW = "duty"


@router.message(Command("duty"), F.chat.type == ChatType.PRIVATE)
async def cmd_duty(message: Message, ctx: UserContext, services: Services) -> None:
    if squad := await ask_squad(message, ctx, services, DUTY_FLOW, *DUTY_MANAGERS):
        await _show_duties(message, squad, ctx, services)


@router.callback_query(SquadPick.filter(F.flow == DUTY_FLOW))
async def on_duty_squad(
    callback: CallbackQuery, callback_data: SquadPick, ctx: UserContext, services: Services
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        squad = await services.squads.get(callback_data.squad_id)
        await _show_duties(message, squad, ctx, services)


async def _show_duties(
    message: Message, squad: SquadDTO, ctx: UserContext, services: Services
) -> None:
    for prompt in await services.duty.manager_prompts(ctx, squad.id):
        await _send(message, prompt)


async def _send(message: Message, prompt: Prompt) -> None:
    await message.answer(prompt.text, reply_markup=to_markup(prompt.keyboard))


@router.callback_query(F.data.startswith(f"{callbacks.DUTY}:"))
async def on_duty_callback(callback: CallbackQuery, ctx: UserContext, services: Services) -> None:
    parsed = callbacks.parse(callback.data or "")
    message = callback_message(callback)
    duty = services.duty
    args = parsed.args

    # шаг 1: вопрос «точно?» — состояние не меняется
    if parsed.action in {
        callbacks.DUTY_REPLACE,
        callbacks.DUTY_PICK,
        callbacks.DUTY_DECLINE,
        callbacks.DUTY_FIX,
        callbacks.DUTY_FIX_PICK,
    }:
        if parsed.action == callbacks.DUTY_REPLACE:
            prompt = await duty.confirm_replace(ctx, args[0])
        elif parsed.action == callbacks.DUTY_PICK:
            prompt = await duty.confirm_pick(ctx, args[0], args[1])
        elif parsed.action == callbacks.DUTY_FIX:
            prompt = await duty.fix_pick_list(ctx, args[0])
        elif parsed.action == callbacks.DUTY_FIX_PICK:
            prompt = await duty.confirm_fix(ctx, args[0], args[1])
        else:
            prompt = await duty.confirm_decline(ctx, args[0])
        await callback.answer()
        if message is not None:
            await _send(message, prompt)
        return

    if parsed.action == callbacks.DUTY_MANUAL:
        delivered = await duty.send_pick_list(ctx, args[0])
        await callback.answer(None if delivered else ru.DM_UNAVAILABLE, show_alert=not delivered)
        return

    # шаг 2: подтверждение или отмена — вопрос заменяется результатом
    if parsed.action == callbacks.DUTY_REPLACE_OK:
        result = await duty.replace(ctx, args[0])
    elif parsed.action == callbacks.DUTY_PICK_OK:
        result = await duty.manual_pick(ctx, args[0], args[1])
    elif parsed.action == callbacks.DUTY_DECLINE_OK:
        result = await duty.decline(ctx, args[0])
    elif parsed.action == callbacks.DUTY_FIX_OK:
        result = await duty.fix(ctx, args[0], args[1])
    else:
        result = ru.CANCELLED
    await callback.answer()
    if message is not None:
        await message.edit_text(result, reply_markup=None)
