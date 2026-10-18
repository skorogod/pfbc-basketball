"""Сбор денег за игру (/game_pay, кнопка «Начать сбор») и реквизиты тренера для перевода.

Шаги сбора: состав → игра → заявка (кто играет и платит, до game_roster_max) → сумма
(стоимость игры ÷ заявка) → срок (сутки / без срока / своя дата) → получатель денег → его
реквизиты (подтвердить; или задать — и сохранить в профиле?) → предпросмотр → «Состав на
матч», пост и опрос «Оплатил» в топике match.
«Начать сбор» (личка, когда «Буду (игрок)» набралось) — сразу заявка и одна кнопка «Готово…»:
стоимость игры у турнира есть и реквизиты сохранены — сбор публикуется сразу (срок по умолчанию,
первые реквизиты); иначе — только недостающее: стоимость игры, реквизиты. Получатель — сам
нажавший; голосование участия закрывается при публикации.
Реквизиты вводятся так же из /profile (тренер): способ → телефон или карта → банк.
"""

import html
from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pfbc_bot import callbacks
from pfbc_bot.bot.helpers import ask_squad, callback_message, dismiss, picked_squad
from pfbc_bot.bot.keyboards.common import (
    Choice,
    ItemAction,
    SquadPick,
    choices_keyboard,
    confirm_markup,
)
from pfbc_bot.bot.states import GamePaymentCreation, PaymentDetailsInput
from pfbc_bot.domain.dates import format_local, localize, parse_local_datetime, to_local, utcnow
from pfbc_bot.domain.payments import format_amount, parse_amount, per_player_amount
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.schemas.payments import (
    MAX_DETAILS,
    GamePaymentCreate,
    PaymentDetails,
    PaymentMethod,
)
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.schemas.users import UserDTO
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict
from pfbc_bot.services.events import format_short
from pfbc_bot.texts import ru

router = Router(name="game_payments")
router.message.filter(F.chat.type == ChatType.PRIVATE)

FLOW = "gp"
COLLECTOR_FLOW = "gp_collector"
AMOUNT_FLOW = "gp_amount"
DEADLINE_FLOW, DETAILS_FLOW, PUBLISH_FLOW = "gp_deadline", "gp_details", "gp_pub"
SAVE_DETAILS_FLOW = "gp_save"
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
    squad = await picked_squad(ctx, services, callback_data.squad_id, Permission.GAME_PAYMENTS)
    await callback.answer()
    if message := callback_message(callback):
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


@router.callback_query(ItemAction.filter((F.kind == "gp") & (F.action == "close")))
async def on_close(callback: CallbackQuery, callback_data: ItemAction) -> None:
    """«Завершить сбор» в сводке /attendance — подтверждение вторым нажатием."""
    await callback.answer()
    if message := callback_message(callback):
        await message.answer(
            ru.GAME_PAYMENT_CONFIRM_CLOSE,
            reply_markup=confirm_markup(
                "gp", "close_ok", callback_data.item_id, ru.PAYMENT_BTN_CONFIRM_CLOSE
            ),
        )


@router.callback_query(ItemAction.filter((F.kind == "gp") & (F.action == "close_ok")))
async def on_close_ok(
    callback: CallbackQuery, callback_data: ItemAction, ctx: UserContext, services: Services
) -> None:
    await callback.answer()
    closed = await services.game_payments.close(ctx, callback_data.item_id)
    if message := callback_message(callback):
        text = ru.GAME_PAYMENT_CLOSED if closed else ru.PAYMENT_ALREADY_CLOSED
        await message.edit_text(text, reply_markup=None)


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
    await _ask_roster(message, ctx, services, state)


# --- «Начать сбор» из уведомления «Буду (игрок)» отметили N» ---
@router.callback_query(F.data.startswith(f"{callbacks.ROSTER}:"))
async def on_roster_start(
    callback: CallbackQuery, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    parsed = callbacks.parse(callback.data or "")
    message = callback_message(callback)
    if parsed.action == callbacks.ROSTER_NO:  # старый вопрос подтверждения
        await callback.answer()
        if message:
            await dismiss(message)
        return
    if parsed.action not in (callbacks.ROSTER_START, callbacks.ROSTER_START_OK):
        await callback.answer()
        return
    event_id = parsed.args[0]
    game_price = await services.game_payments.check_start(ctx, event_id)
    await callback.answer()
    if message is None:
        return
    event = await services.events.get(event_id)
    squad = await services.squads.get(event.squad_id)
    saved = await services.game_payments.details_of(ctx.user.id)
    deadline = await services.game_payments.default_deadline(ctx, squad.id)
    await state.clear()
    await state.update_data(
        event_id=event.id,
        squad_id=squad.id,
        tz=squad.timezone,
        collector_id=ctx.user.id,  # деньги получает нажавший
        from_start=True,  # уже объявленный сбор не заменяется
        game_price=str(game_price) if game_price is not None else None,
        details=saved[0].model_dump(mode="json") if saved else None,  # первые сохранённые
        deadline_hint=format_local(deadline, squad.timezone),
    )
    await _ask_roster(message, ctx, services, state)


START_PUBLISH, START_PRICE, START_DETAILS = "publish", "price", "details"


def _start_step(data: dict[str, object]) -> str:
    """Что нужно после заявки: стоимость игры, реквизиты — или уже можно публиковать."""
    if not data.get("game_price"):
        return START_PRICE
    if not data.get("details"):
        return START_DETAILS
    return START_PUBLISH


def _start_summary(data: dict[str, object]) -> str:
    """Каким будет сбор: сумма с игрока, срок, реквизиты (или что ещё указать)."""
    players = len(data["roster"])  # type: ignore[arg-type]
    lines = []
    if price := data.get("game_price"):
        game_price = Decimal(str(price))
        lines.append(
            ru.ROSTER_START_AMOUNT.format(
                amount=format_amount(per_player_amount(game_price, players)) if players else "—",
                price=format_amount(game_price),
                players=players,
            )
        )
    else:
        lines.append(ru.ROSTER_START_NO_PRICE)
    lines.append(ru.ROSTER_START_DEADLINE.format(when=data["deadline_hint"]))
    if details := data.get("details"):
        text = PaymentDetails.model_validate(details).text
        lines.append(ru.ROSTER_START_DETAILS.format(details=html.escape(text)))
    else:
        lines.append(ru.ROSTER_START_NO_DETAILS)
    return "\n".join(lines) + "\n" + ru.ROSTER_START_FOOTER


async def _ask_start_confirm(message: Message, state: FSMContext) -> None:
    """Всё известно — сводка и одна кнопка «Готово. Начать сбор»."""
    await state.set_state(GamePaymentCreation.confirm)
    data = await state.get_data()
    await message.answer(
        ru.ROSTER_START_SUMMARY.format(
            title=html.escape(str(data["title"])),
            players=len(data["roster"]),
            lines=_start_summary(data),
        ),
        reply_markup=choices_keyboard(PUBLISH_FLOW, [("start", ru.ROSTER_BTN_START_NOW)]),
    )


async def _publish_from_start(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    """Сбор из «Начать сбор»: сумма — стоимость игры ÷ заявка, срок по умолчанию."""
    data = await state.get_data()
    await state.clear()  # до публикации: двойное нажатие не создаст второй сбор
    deadline = await services.game_payments.default_deadline(ctx, int(data["squad_id"]))
    data["amount"] = str(per_player_amount(Decimal(data["game_price"]), len(data["roster"])))
    data["deadline"] = deadline.isoformat()
    await services.game_payments.create(ctx, _payment_data(data))
    await message.answer(ru.GAME_PAYMENT_CREATED)


async def _ask_start_details(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    collector = await services.game_payments.collector(
        ctx, int((await state.get_data())["squad_id"]), ctx.user.id
    )
    await _ask_details(message, ctx, services, state, collector)


async def _after_details(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    """Реквизиты выбраны или введены: из «Начать сбор» — кнопка старта, иначе предпросмотр."""
    if (await state.get_data()).get("from_start"):
        await _ask_start_confirm(message, state)
    else:
        await _preview(message, ctx, services, state)


@router.message(GamePaymentCreation.game_price, F.text)
async def on_game_price(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    try:
        price = parse_amount(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(game_price=str(price))  # только для этого сбора, турнир не меняется
    if _start_step(await state.get_data()) == START_DETAILS:
        await _ask_start_details(message, ctx, services, state)
    else:
        await _ask_start_confirm(message, state)


# --- заявка: кто играет и платит ---
class RosterPick(CallbackData, prefix="rp"):
    action: str  # t — включить/убрать игрока | more | less | done
    user_id: int = 0


async def _ask_roster(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    event_id = int((await state.get_data())["event_id"])
    choice = await services.game_payments.roster(ctx, event_id)
    await state.set_state(GamePaymentCreation.roster)
    await state.update_data(
        roster=choice.preselected,
        voted=[c.user.id for c in choice.candidates if c.voted],
        others=[c.user.id for c in choice.candidates if not c.voted],
        names={str(c.user.id): c.user.display_name for c in choice.candidates},
        roster_max=choice.max_players,
        title=choice.event.title,
        show_all=not any(c.voted for c in choice.candidates),
    )
    data = await state.get_data()
    await message.answer(_roster_text(data), reply_markup=_roster_markup(data))


def _roster_text(data: dict[str, object]) -> str:
    roster = list(data["roster"])  # type: ignore[call-overload]
    text = ru.ROSTER_CHOOSE.format(
        title=html.escape(str(data["title"])), count=len(roster), max=data["roster_max"]
    )
    if data.get("from_start"):
        text += "\n\n" + _start_summary(data)
    return text


def _roster_markup(data: dict[str, object]) -> InlineKeyboardMarkup:
    roster = set(data["roster"])  # type: ignore[call-overload]
    names: dict[str, str] = data["names"]  # type: ignore[assignment]
    voted: list[int] = data["voted"]  # type: ignore[assignment]
    others: list[int] = data["others"]  # type: ignore[assignment]
    show_all = bool(data.get("show_all"))
    builder = InlineKeyboardBuilder()
    shown = [*voted, *others] if show_all else voted
    for uid in shown:
        mark = "✅" if uid in roster else "▫️"
        hint = "" if uid in voted else " ·"  # не отвечал «Буду (игрок)»
        builder.button(
            text=f"{mark} {names[str(uid)]}{hint}"[:60],
            callback_data=RosterPick(action="t", user_id=uid),
        )
    if others:
        builder.button(
            text=ru.ROSTER_BTN_LESS if show_all and voted else ru.ROSTER_BTN_MORE,
            callback_data=RosterPick(action="less" if show_all else "more"),
        )
    if data.get("from_start"):
        done = {
            START_PUBLISH: ru.ROSTER_BTN_START_NOW,
            START_PRICE: ru.ROSTER_BTN_ASK_PRICE,
            START_DETAILS: ru.ROSTER_BTN_ASK_DETAILS,
        }[_start_step(data)]
    else:
        done = ru.ROSTER_BTN_DONE.format(count=len(roster))
    builder.button(text=done, callback_data=RosterPick(action="done"))
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(GamePaymentCreation.roster, RosterPick.filter())
async def on_roster_pick(
    callback: CallbackQuery,
    callback_data: RosterPick,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    message = callback_message(callback)
    data = await state.get_data()
    roster: list[int] = list(data["roster"])
    if callback_data.action == "done":
        if not roster:
            await callback.answer(ru.ROSTER_EMPTY, show_alert=True)
            return
        await callback.answer()
        if message is None:
            return
        await message.edit_reply_markup(reply_markup=None)
        if not data.get("from_start"):
            await _ask_amount(message, ctx, services, state)
            return
        step = _start_step(data)
        if step == START_PUBLISH:
            await _publish_from_start(message, ctx, services, state)
        elif step == START_PRICE:
            await state.set_state(GamePaymentCreation.game_price)
            await message.answer(ru.GAME_PAYMENT_ASK_GAME_PRICE)
        else:
            await _ask_start_details(message, ctx, services, state)
        return
    if callback_data.action == "t":
        uid = callback_data.user_id
        if uid not in (*data["voted"], *data["others"]):
            await callback.answer()
            return
        if uid in roster:
            roster.remove(uid)
        elif len(roster) >= int(data["roster_max"]):
            await callback.answer(ru.ROSTER_LIMIT.format(max=data["roster_max"]), show_alert=True)
            return
        else:
            roster.append(uid)
        await state.update_data(roster=roster)
    elif callback_data.action in ("more", "less"):
        await state.update_data(show_all=callback_data.action == "more")
    await callback.answer()
    if message is not None:
        data = await state.get_data()
        await message.edit_text(_roster_text(data), reply_markup=_roster_markup(data))


# --- сумма: стоимость игры ÷ заявка ---
async def _ask_amount(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    data = await state.get_data()
    players = len(data["roster"])
    await state.set_state(GamePaymentCreation.amount)
    # матч турнира со стоимостью игры — сумма с игрока одной кнопкой
    suggestion = await services.game_payments.suggest_amount(ctx, int(data["event_id"]), players)
    text, markup = ru.GAME_PAYMENT_ASK_AMOUNT, None
    if suggestion is not None:
        await state.update_data(suggested=str(suggestion.per_player))
        text = (
            ru.GAME_PAYMENT_AMOUNT_CALC.format(
                players=players,
                price=format_amount(suggestion.game_price),
                amount=format_amount(suggestion.per_player),
            )
            + "\n\n"
            + text
        )
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
    await message.answer(text, reply_markup=markup)


@router.message(GamePaymentCreation.amount, F.text)
async def on_amount(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    try:
        amount = parse_amount(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await _set_amount(message, ctx, services, state, amount)


@router.callback_query(GamePaymentCreation.amount, Choice.filter(F.flow == AMOUNT_FLOW))
async def on_amount_suggested(
    callback: CallbackQuery, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    await callback.answer()
    message = callback_message(callback)
    suggested = (await state.get_data()).get("suggested")
    if message is None or suggested is None:
        return
    await message.edit_reply_markup(reply_markup=None)
    await _set_amount(message, ctx, services, state, Decimal(suggested))


async def _set_amount(
    message: Message, ctx: UserContext, services: Services, state: FSMContext, amount: Decimal
) -> None:
    await state.update_data(amount=str(amount))
    await state.set_state(GamePaymentCreation.deadline)
    data = await state.get_data()
    deadline = await services.game_payments.default_deadline(ctx, int(data["squad_id"]))
    day = format_local(deadline, str(data["tz"]))
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
    deadline = None
    if callback_data.value == "day":
        squad_id = int((await state.get_data())["squad_id"])
        deadline = await services.game_payments.default_deadline(ctx, squad_id)
    await state.update_data(deadline=deadline.isoformat() if deadline else None)
    await _ask_collector(message, ctx, services, state)


async def _ask_collector(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    """Кто получает деньги: создающий или тренер/админ состава со своими реквизитами.
    Из «Начать сбор» — сам нажавший, без вопроса."""
    data = await state.get_data()
    squad_id = int(data["squad_id"])
    if data.get("from_start"):
        collector = await services.game_payments.collector(ctx, squad_id, ctx.user.id)
        await _ask_details(message, ctx, services, state, collector)
        return
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
    """Реквизиты получателя показываются полностью — проверить и выбрать. Свои — ещё и
    «Ввести новые» (нет сохранённых — сразу ввод); чужие задаёт только их владелец."""
    saved = await services.game_payments.details_of(collector.id)
    mine = collector.id == ctx.user.id
    name = collector.profile_html
    if not saved:
        await message.answer(ru.GAME_PAYMENT_COLLECTOR_NO_DETAILS.format(name=name))
        await start_details_input(message, state, PURPOSE_GAME, phone=collector.phone)
        return
    await state.set_state(GamePaymentCreation.details)
    await state.update_data(
        saved_details=[d.model_dump(mode="json") for d in saved], phone_hint=collector.phone
    )
    numbered = "\n".join(f"{n}. {d.text}" for n, d in enumerate(saved, 1))
    template = ru.GAME_PAYMENT_COLLECTOR_DETAILS if mine else ru.GAME_PAYMENT_OTHER_DETAILS
    single = ru.GAME_PAYMENT_BTN_DETAILS_OK if mine else ru.GAME_PAYMENT_BTN_USE
    options = [
        (d.id, ru.GAME_PAYMENT_BTN_USE_N.format(n=n, details=d.short) if len(saved) > 1 else single)
        for n, d in enumerate(saved, 1)
    ]
    if mine:
        options.append(("new", ru.GAME_PAYMENT_BTN_NEW_DETAILS))
    await message.answer(
        template.format(name=name, details=numbered),
        reply_markup=choices_keyboard(DETAILS_FLOW, options, width=1),
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
    data = await state.get_data()
    if callback_data.value == "new":
        if data.get("collector_id") != ctx.user.id:  # чужие реквизиты не вводятся
            return
        await message.edit_reply_markup(reply_markup=None)
        await start_details_input(message, state, PURPOSE_GAME, phone=data.get("phone_hint"))
        return
    chosen = next((d for d in data["saved_details"] if d["id"] == callback_data.value), None)
    if chosen is None:
        return
    await message.edit_reply_markup(reply_markup=None)
    await state.update_data(details=chosen)
    await _after_details(message, ctx, services, state)


# --- мои реквизиты (из /profile): список, добавить, изменить, удалить ---
class DetailsAction(CallbackData, prefix="pd"):
    action: str  # add | edit | del | delok | no
    details_id: str = ""


async def show_my_details(message: Message, ctx: UserContext, services: Services) -> None:
    saved = await services.game_payments.details_of(ctx.user.id)
    builder = InlineKeyboardBuilder()
    for n, d in enumerate(saved, 1):
        builder.button(
            text=ru.DETAILS_BTN_EDIT.format(n=n),
            callback_data=DetailsAction(action="edit", details_id=d.id),
        )
        builder.button(
            text=ru.DETAILS_BTN_DELETE.format(n=n),
            callback_data=DetailsAction(action="del", details_id=d.id),
        )
    if len(saved) < MAX_DETAILS:
        builder.button(text=ru.DETAILS_BTN_ADD, callback_data=DetailsAction(action="add"))
    builder.adjust(*([2] * len(saved)), 1)
    lines = "\n".join(f"{n}. {d.text}" for n, d in enumerate(saved, 1)) or ru.DETAILS_EMPTY
    await message.answer(ru.DETAILS_MINE.format(details=lines), reply_markup=builder.as_markup())


@router.callback_query(DetailsAction.filter())
async def on_details_action(
    callback: CallbackQuery,
    callback_data: DetailsAction,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    message = callback_message(callback)
    action, details_id = callback_data.action, callback_data.details_id
    if action == "delok":  # шаг 2: подтверждено
        await services.game_payments.delete_details(ctx, details_id)
        await callback.answer(ru.DETAILS_DELETED)
        if message:
            await message.edit_text(ru.DETAILS_DELETED, reply_markup=None)
            await show_my_details(message, ctx, services)
        return
    await callback.answer()
    if message is None:
        return
    if action == "no":
        await dismiss(message)
        return
    if action == "add":
        await state.clear()
        await start_details_input(message, state, PURPOSE_PROFILE, phone=ctx.user.phone)
        return
    saved = await services.game_payments.details_of(ctx.user.id)
    details = next((d for d in saved if d.id == details_id), None)
    if details is None:
        await message.answer(ru.DETAILS_NOT_FOUND)
        return
    if action == "edit":
        await state.clear()
        await message.answer(ru.DETAILS_EDITING.format(details=details.text))
        await start_details_input(
            message, state, PURPOSE_PROFILE, phone=ctx.user.phone, details_id=details.id
        )
    elif action == "del":  # шаг 1: вопрос — ничего не меняет
        builder = InlineKeyboardBuilder()
        builder.button(
            text=ru.DETAILS_BTN_CONFIRM_DELETE,
            callback_data=DetailsAction(action="delok", details_id=details.id),
        )
        builder.button(text=ru.BTN_CANCEL, callback_data=DetailsAction(action="no"))
        builder.adjust(2)
        await message.answer(
            ru.DETAILS_CONFIRM_DELETE.format(details=details.text),
            reply_markup=builder.as_markup(),
        )


# --- ввод реквизитов (и для сбора, и из /profile) ---
async def start_details_input(
    message: Message,
    state: FSMContext,
    purpose: str,
    *,
    phone: str | None,
    details_id: str | None = None,
) -> None:
    """phone — свой телефон из профиля: предлагается кнопкой; details_id — изменяем эти."""
    await state.set_state(PaymentDetailsInput.method)
    await state.update_data(details_purpose=purpose, phone_hint=phone, details_id=details_id)
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
            MY_PHONE_FLOW, [("mine", ru.DETAILS_BTN_MY_PHONE.format(phone=phone))]
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
        await state.clear()
        if data.get("details_id"):
            await services.game_payments.update_details(ctx, str(data["details_id"]), details)
        else:
            await services.game_payments.add_details(ctx, details)
        await message.answer(ru.DETAILS_SAVED.format(details=details.text))
        await show_my_details(message, ctx, services)
        return
    await state.update_data(details=details.model_dump(mode="json"))
    # свои новые реквизиты — сохранить в профиле? (сами по себе не запоминаются)
    await state.set_state(GamePaymentCreation.save_details)
    await message.answer(
        ru.GAME_PAYMENT_ASK_SAVE_DETAILS.format(details=details.text),
        reply_markup=choices_keyboard(SAVE_DETAILS_FLOW, [("yes", ru.BTN_YES), ("no", ru.BTN_NO)]),
    )


@router.callback_query(GamePaymentCreation.save_details, Choice.filter(F.flow == SAVE_DETAILS_FLOW))
async def on_save_details(
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
    if callback_data.value == "yes":
        details = PaymentDetails.model_validate((await state.get_data())["details"])
        try:
            await services.game_payments.add_details(ctx, details)
            await message.answer(ru.GAME_PAYMENT_DETAILS_SAVED)
        except Conflict as exc:  # лимит реквизитов — сбор продолжается без сохранения
            await message.answer(str(exc))
    await _after_details(message, ctx, services, state)


# --- предпросмотр и публикация ---
def _payment_data(data: dict[str, object]) -> GamePaymentCreate:
    deadline = data.get("deadline")
    return GamePaymentCreate(
        match_event_id=int(str(data["event_id"])),
        collector_user_id=int(str(data["collector_id"])),
        amount=Decimal(str(data["amount"])),
        deadline_at=datetime.fromisoformat(str(deadline)) if deadline else None,
        details=PaymentDetails.model_validate(data["details"]),
        player_ids=list(data["roster"]),  # type: ignore[call-overload]
        replace_active=not data.get("from_start"),
    )


async def _preview(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    preview = await services.game_payments.preview(ctx, _payment_data(await state.get_data()))
    await state.set_state(GamePaymentCreation.confirm)
    await message.answer(
        ru.GAME_PAYMENT_PREVIEW.format(roster=preview.roster, post=preview.post),
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
    if message is not None and callback_data.value == "start":  # «Готово. Начать сбор»
        await message.edit_reply_markup(reply_markup=None)
        await _publish_from_start(message, ctx, services, state)
        return
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
