"""Квитанции об оплате тренировок за месяц (/receipts): опрос «Отправил» вручную.

Шаги: состав → если опрос этого месяца уже есть — кто отметился (повторно не публикуется) →
срок (по умолчанию или своя дата) → пост и опрос в группе.
"""

from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pfbc_bot.bot.helpers import ask_squad, callback_message, picked_squad
from pfbc_bot.bot.keyboards.common import (
    Choice,
    ItemAction,
    SquadPick,
    choices_keyboard,
    confirm_markup,
)
from pfbc_bot.bot.states import ReceiptRequestCreation
from pfbc_bot.domain.dates import format_local, localize, parse_local_datetime, to_local, utcnow
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.texts import ru

router = Router(name="receipts")
router.message.filter(F.chat.type == ChatType.PRIVATE)

FLOW, DEADLINE_FLOW = "rc", "rc_deadline"


@router.message(Command("receipts"))
async def cmd_receipts(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    if squad := await ask_squad(message, ctx, services, FLOW, Permission.RECEIPTS):
        await _start(message, squad, ctx, services, state)


@router.callback_query(SquadPick.filter(F.flow == FLOW))
async def on_squad(
    callback: CallbackQuery,
    callback_data: SquadPick,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    squad = await picked_squad(ctx, services, callback_data.squad_id, Permission.RECEIPTS)
    await callback.answer()
    if message := callback_message(callback):
        await _start(message, squad, ctx, services, state)


async def _start(
    message: Message, squad: SquadDTO, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    if (current := await services.receipts.current(ctx, squad.id)) is not None:
        if current.closed:
            await message.answer(current.text + ru.PAYMENT_CLOSED_MARK)
            return
        builder = InlineKeyboardBuilder()
        builder.button(
            text=ru.PAYMENT_BTN_CLOSE,
            callback_data=ItemAction(kind="rc", action="close", item_id=current.request_id),
        )
        await message.answer(current.text, reply_markup=builder.as_markup())
        return
    deadline = await services.receipts.default_deadline(ctx, squad.id)
    await state.clear()
    await state.update_data(squad_id=squad.id, tz=squad.timezone)
    await state.set_state(ReceiptRequestCreation.deadline)
    button = ru.RECEIPT_BTN_DEFAULT.format(when=format_local(deadline, squad.timezone))
    await message.answer(
        ru.RECEIPT_ASK_DEADLINE,
        reply_markup=choices_keyboard(DEADLINE_FLOW, [("default", button)], width=1),
    )


@router.callback_query(ItemAction.filter((F.kind == "rc") & (F.action == "close")))
async def on_close(callback: CallbackQuery, callback_data: ItemAction) -> None:
    """«Завершить сбор» — подтверждение вторым нажатием."""
    await callback.answer()
    if message := callback_message(callback):
        await message.answer(
            ru.RECEIPT_CONFIRM_CLOSE,
            reply_markup=confirm_markup(
                "rc", "close_ok", callback_data.item_id, ru.PAYMENT_BTN_CONFIRM_CLOSE
            ),
        )


@router.callback_query(ItemAction.filter((F.kind == "rc") & (F.action == "close_ok")))
async def on_close_ok(
    callback: CallbackQuery, callback_data: ItemAction, ctx: UserContext, services: Services
) -> None:
    await callback.answer()
    month = await services.receipts.close(ctx, callback_data.item_id)
    if message := callback_message(callback):
        text = ru.RECEIPT_CLOSED.format(month=month) if month else ru.PAYMENT_ALREADY_CLOSED
        await message.edit_text(text, reply_markup=None)


@router.message(ReceiptRequestCreation.deadline, F.text)
async def on_deadline_text(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    tz = str((await state.get_data())["tz"])
    try:
        naive = parse_local_datetime(message.text or "", today=to_local(utcnow(), tz).date())
    except ValueError as exc:
        await message.answer(str(exc))
        return
    deadline = localize(naive, tz)
    if deadline <= utcnow():
        await message.answer(ru.PAST_DATE)
        return
    await _publish(message, ctx, services, state, deadline)


@router.callback_query(ReceiptRequestCreation.deadline, Choice.filter(F.flow == DEADLINE_FLOW))
async def on_deadline_default(
    callback: CallbackQuery, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    await message.edit_reply_markup(reply_markup=None)
    squad_id = int((await state.get_data())["squad_id"])
    deadline = await services.receipts.default_deadline(ctx, squad_id)
    await _publish(message, ctx, services, state, deadline)


async def _publish(
    message: Message,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
    deadline: datetime,
) -> None:
    squad_id = int((await state.get_data())["squad_id"])
    await state.clear()
    month = await services.receipts.create(ctx, squad_id, deadline)
    await message.answer(ru.RECEIPT_CREATED.format(month=month))
