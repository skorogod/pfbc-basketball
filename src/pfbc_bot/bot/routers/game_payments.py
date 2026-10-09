"""Сбор денег за игру (/game_pay) и реквизиты тренера для перевода.

Шаги сбора: состав → игра → сумма → срок (сутки / без срока / своя дата) → получатель денег →
его реквизиты (показываются для проверки; или новые) → предпросмотр → пост и опрос «Оплатил»
в топике «Игры».
Реквизиты вводятся так же из /profile (тренер): способ → телефон или карта → банк.
"""

import html
from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pfbc_bot.bot.helpers import ask_squad, callback_message, dismiss
from pfbc_bot.bot.keyboards.common import Choice, ItemAction, SquadPick, choices_keyboard
from pfbc_bot.bot.states import GamePaymentCreation, PaymentDetailsInput
from pfbc_bot.domain.dates import format_local, localize, parse_local_datetime, to_local, utcnow
from pfbc_bot.domain.payments import format_amount, format_phone, parse_amount
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.schemas.payments import GamePaymentCreate, PaymentDetails, PaymentMethod
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.schemas.users import UserDTO
from pfbc_bot.services.container import Services
from pfbc_bot.services.events import format_short
from pfbc_bot.services.game_payments import DEFAULT_DEADLINE
from pfbc_bot.texts import ru

router = Router(name="game_payments")
router.message.filter(F.chat.type == ChatType.PRIVATE)

FLOW = "gp"
COLLECTOR_FLOW = "gp_collector"
AMOUNT_FLOW = "gp_amount"
DEADLINE_FLOW, DETAILS_FLOW, PUBLISH_FLOW = "gp_deadline", "gp_details", "gp_pub"
METHOD_FLOW, MY_PHONE_FLOW = "pd_method", "pd_phone"
PURPOSE_GAME, PURPOSE_PROFILE = "game", "profile"


@router.message(Command("game_pay"))
async def cmd_game_pay(message: Message, ctx: UserContext, services: Services) -> None:
    if squad := await ask_squad(message, ctx, services, FLOW, Permission.GAME_PAYMENTS):
        await _choose_match(message, squad, ctx, services)


@router.callback_query(SquadPick.filter(F.flow == FLOW))
async def on_squad(
    callback: CallbackQuery, callback_data: SquadPick, ctx: UserContext, services: Services
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        squad = await services.squads.get(callback_data.squad_id)
        await _choose_match(message, squad, ctx, services)


async def _choose_match(
    message: Message, squad: SquadDTO, ctx: UserContext, services: Services
) -> None:
    matches = await services.game_payments.matches(ctx, squad.id)
    if not matches:
        await message.answer(ru.GAME_PAYMENT_NO_MATCHES)
        return
    builder = InlineKeyboardBuilder()
    for match in matches:
        builder.button(
            text=f"🏀 {format_short(match, squad.timezone)} {match.title}"[:60],
            callback_data=ItemAction(kind="gp", action="match", item_id=match.id),
        )
    builder.adjust(1)
    await message.answer(ru.GAME_PAYMENT_CHOOSE_MATCH, reply_markup=builder.as_markup())


@router.callback_query(ItemAction.filter((F.kind == "gp") & (F.action == "match")))
async def on_match(
    callback: CallbackQuery,
    callback_data: ItemAction,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    event = await services.events.get(callback_data.item_id)
    squad = await services.squads.get(event.squad_id)
    await state.clear()
    await state.update_data(event_id=event.id, squad_id=squad.id, tz=squad.timezone)
    await state.set_state(GamePaymentCreation.amount)
    # матч турнира со стоимостью игры — сумма с игрока одной кнопкой
    suggestion = await services.game_payments.suggest_amount(ctx, event.id)
    markup = None
    if suggestion is not None:
        await state.update_data(suggested=str(suggestion.per_player))
        markup = choices_keyboard(
            AMOUNT_FLOW,
            [
                (
                    "suggested",
                    ru.GAME_PAYMENT_BTN_SUGGESTED.format(
                        amount=format_amount(suggestion.per_player),
                        price=format_amount(suggestion.game_price),
                        players=suggestion.players,
                    ),
                )
            ],
        )
    await message.answer(ru.GAME_PAYMENT_ASK_AMOUNT, reply_markup=markup)


@router.message(GamePaymentCreation.amount, F.text)
async def on_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = parse_amount(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await _set_amount(message, state, amount)


@router.callback_query(GamePaymentCreation.amount, Choice.filter(F.flow == AMOUNT_FLOW))
async def on_amount_suggested(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    message = callback_message(callback)
    suggested = (await state.get_data()).get("suggested")
    if message is None or suggested is None:
        return
    await message.edit_reply_markup(reply_markup=None)
    await _set_amount(message, state, Decimal(suggested))


async def _set_amount(message: Message, state: FSMContext, amount: Decimal) -> None:
    await state.update_data(amount=str(amount))
    await state.set_state(GamePaymentCreation.deadline)
    tz = str((await state.get_data())["tz"])
    day = format_local(utcnow() + DEFAULT_DEADLINE, tz)
    await message.answer(
        ru.GAME_PAYMENT_ASK_DEADLINE,
        reply_markup=choices_keyboard(
            DEADLINE_FLOW,
            [
                ("day", ru.GAME_PAYMENT_BTN_DAY.format(when=day)),
                ("none", ru.GAME_PAYMENT_BTN_NO_DEADLINE),
            ],
            width=1,
        ),
    )


@router.message(GamePaymentCreation.deadline, F.text)
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
    await state.update_data(deadline=deadline.isoformat())
    await _ask_collector(message, ctx, services, state)


@router.callback_query(GamePaymentCreation.deadline, Choice.filter(F.flow == DEADLINE_FLOW))
async def on_deadline_choice(
    callback: CallbackQuery,
    callback_data: Choice,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    await message.edit_reply_markup(reply_markup=None)
    deadline = utcnow() + DEFAULT_DEADLINE if callback_data.value == "day" else None
    await state.update_data(deadline=deadline.isoformat() if deadline else None)
    await _ask_collector(message, ctx, services, state)


async def _ask_collector(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    """Кто получает деньги: создающий или тренер/админ состава со своими реквизитами."""
    squad_id = int((await state.get_data())["squad_id"])
    people = await services.game_payments.collectors(ctx, squad_id)
    await state.set_state(GamePaymentCreation.collector)
    options = [
        (str(u.id), u.display_name + (ru.GAME_PAYMENT_ME if u.id == ctx.user.id else ""))
        for u in people
    ]
    await message.answer(
        ru.GAME_PAYMENT_ASK_COLLECTOR + ru.GAME_PAYMENT_ASK_COLLECTOR_HINT,
        reply_markup=choices_keyboard(COLLECTOR_FLOW, options, width=1),
    )


@router.callback_query(GamePaymentCreation.collector, Choice.filter(F.flow == COLLECTOR_FLOW))
async def on_collector(
    callback: CallbackQuery,
    callback_data: Choice,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    squad_id = int((await state.get_data())["squad_id"])
    collector = await services.game_payments.collector(ctx, squad_id, int(callback_data.value))
    await message.edit_reply_markup(reply_markup=None)
    await state.update_data(collector_id=collector.id)
    await _ask_details(message, ctx, services, state, collector)


async def _ask_details(
    message: Message,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
    collector: UserDTO,
) -> None:
    """Реквизиты получателя показываются полностью — проверить. Свои — использовать или ввести
    новые (нет — сразу ввод); чужие задаёт только их владелец — только «Использовать»."""
    saved = await services.game_payments.saved_details(collector.id)
    name = collector.profile_html
    if collector.id != ctx.user.id and saved is not None:
        await state.set_state(GamePaymentCreation.details)
        await state.update_data(saved_details=saved.model_dump(mode="json"))
        await message.answer(
            ru.GAME_PAYMENT_OTHER_DETAILS.format(name=name, details=saved.text),
            reply_markup=choices_keyboard(DETAILS_FLOW, [("saved", ru.GAME_PAYMENT_BTN_USE)]),
        )
        return
    if saved is None:
        await message.answer(ru.GAME_PAYMENT_COLLECTOR_NO_DETAILS.format(name=name))
        await start_details_input(message, state, PURPOSE_GAME, phone=collector.phone)
        return
    await state.set_state(GamePaymentCreation.details)
    await state.update_data(saved_details=saved.model_dump(mode="json"), phone_hint=collector.phone)
    await message.answer(
        ru.GAME_PAYMENT_COLLECTOR_DETAILS.format(name=name, details=saved.text),
        reply_markup=choices_keyboard(
            DETAILS_FLOW,
            [("saved", ru.GAME_PAYMENT_BTN_USE), ("new", ru.GAME_PAYMENT_BTN_NEW_DETAILS)],
            width=1,
        ),
    )


@router.callback_query(GamePaymentCreation.details, Choice.filter(F.flow == DETAILS_FLOW))
async def on_details_choice(
    callback: CallbackQuery,
    callback_data: Choice,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    await message.edit_reply_markup(reply_markup=None)
    data = await state.get_data()
    if callback_data.value == "new" and data.get("collector_id") == ctx.user.id:
        await start_details_input(message, state, PURPOSE_GAME, phone=data.get("phone_hint"))
        return
    await state.update_data(details=data["saved_details"])
    await _preview(message, ctx, services, state)


# --- ввод реквизитов (и для сбора, и из /profile) ---
async def start_details_input(
    message: Message, state: FSMContext, purpose: str, *, phone: str | None
) -> None:
    """phone — телефон того, чьи реквизиты вводим: предлагается кнопкой."""
    await state.set_state(PaymentDetailsInput.method)
    await state.update_data(details_purpose=purpose, phone_hint=phone)
    await message.answer(
        ru.DETAILS_ASK_METHOD,
        reply_markup=choices_keyboard(
            METHOD_FLOW,
            [
                (PaymentMethod.PHONE.value, ru.DETAILS_BTN_PHONE),
                (PaymentMethod.CARD.value, ru.DETAILS_BTN_CARD),
            ],
            width=1,
        ),
    )


@router.callback_query(PaymentDetailsInput.method, Choice.filter(F.flow == METHOD_FLOW))
async def on_method(callback: CallbackQuery, callback_data: Choice, state: FSMContext) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    await message.edit_reply_markup(reply_markup=None)
    method = PaymentMethod(callback_data.value)
    await state.update_data(method=method.value)
    await state.set_state(PaymentDetailsInput.value)
    if method == PaymentMethod.CARD:
        await message.answer(ru.DETAILS_ASK_CARD)
        return
    # телефон из профиля — по умолчанию, одной кнопкой
    phone = (await state.get_data()).get("phone_hint")
    markup = None
    if phone:
        markup = choices_keyboard(
            MY_PHONE_FLOW, [("mine", ru.DETAILS_BTN_MY_PHONE.format(phone=format_phone(phone)))]
        )
    await message.answer(ru.DETAILS_ASK_PHONE, reply_markup=markup)


@router.message(PaymentDetailsInput.value, F.text)
async def on_details_value(message: Message, state: FSMContext) -> None:
    method = PaymentMethod((await state.get_data())["method"])
    try:  # та же проверка, что и в PaymentDetails
        PaymentDetails(method=method, value=message.text or "", bank="банк")
    except ValueError as exc:
        await message.answer(_first_error(exc))
        return
    await _ask_bank(message, state, message.text or "")


@router.callback_query(PaymentDetailsInput.value, Choice.filter(F.flow == MY_PHONE_FLOW))
async def on_my_phone(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    message = callback_message(callback)
    phone = (await state.get_data()).get("phone_hint")
    if message is None or not phone:
        return
    await message.edit_reply_markup(reply_markup=None)
    await _ask_bank(message, state, str(phone))


async def _ask_bank(message: Message, state: FSMContext, value: str) -> None:
    await state.update_data(value=value)
    await state.set_state(PaymentDetailsInput.bank)
    await message.answer(ru.DETAILS_ASK_BANK)


@router.message(PaymentDetailsInput.bank, F.text)
async def on_bank(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    data = await state.get_data()
    try:
        details = PaymentDetails(
            method=PaymentMethod(data["method"]), value=data["value"], bank=message.text or ""
        )
    except ValueError as exc:
        await message.answer(_first_error(exc))
        return
    if data.get("details_purpose") == PURPOSE_PROFILE:
        await services.game_payments.save_details(ctx, details)
        await state.clear()
        await message.answer(ru.DETAILS_SAVED.format(details=details.text))
        return
    await state.update_data(details=details.model_dump(mode="json"))
    await _preview(message, ctx, services, state)


# --- предпросмотр и публикация ---
def _payment_data(data: dict[str, object]) -> GamePaymentCreate:
    deadline = data.get("deadline")
    return GamePaymentCreate(
        match_event_id=int(str(data["event_id"])),
        collector_user_id=int(str(data["collector_id"])),
        amount=Decimal(str(data["amount"])),
        deadline_at=datetime.fromisoformat(str(deadline)) if deadline else None,
        details=PaymentDetails.model_validate(data["details"]),
    )


async def _preview(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    post = await services.game_payments.preview(ctx, _payment_data(await state.get_data()))
    await state.set_state(GamePaymentCreation.confirm)
    await message.answer(
        ru.GAME_PAYMENT_PREVIEW.format(post=post),
        reply_markup=choices_keyboard(
            PUBLISH_FLOW, [("yes", ru.GAME_PAYMENT_BTN_PUBLISH), ("no", ru.BTN_CANCEL)]
        ),
    )


@router.callback_query(GamePaymentCreation.confirm, Choice.filter(F.flow == PUBLISH_FLOW))
async def on_publish(
    callback: CallbackQuery,
    callback_data: Choice,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    await callback.answer()
    message = callback_message(callback)
    data = await state.get_data()
    await state.clear()  # до публикации: двойное нажатие не создаст второй сбор
    if message is None:
        return
    if callback_data.value != "yes":
        await dismiss(message)
        return
    await services.game_payments.create(ctx, _payment_data(data))
    await message.edit_text(ru.GAME_PAYMENT_CREATED, reply_markup=None)


def _first_error(exc: ValueError) -> str:
    """Текст ошибки проверки без служебной обвязки pydantic."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        for error in errors():
            message = str(error.get("msg", ""))
            return html.escape(message.removeprefix("Value error, "))
    return html.escape(str(exc))
