"""Личная анкета и управление отчётами /postmatch."""

from html import unescape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from pfbc_bot.bot.helpers import callback_message
from pfbc_bot.domain.postmatch import PAIN_DETAIL
from pfbc_bot.integrations.telegram.notifier import to_markup
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import Conflict
from pfbc_bot.services.ports import InlineButton, Keyboard

router = Router(name="postmatch")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class SurveyInput(StatesGroup):
    text = State()
    dates = State()


async def send_options(message: Message, text: str, buttons: list[tuple[str, str]]) -> None:
    lines: list[str] = []
    for line in text.splitlines():
        if sum(map(len, lines)) + len(lines) + len(line) > 3500:
            await message.answer("\n".join(lines))
            lines = []
        lines.append(line)
    kb = [[InlineButton(label[:64], data)] for label, data in buttons]
    await message.answer("\n".join(lines) or "Выберите действие.", reply_markup=to_markup(kb[:80]))
    for offset in range(80, len(kb), 80):
        await message.answer(
            "📋 <b>Продолжение списка</b>", reply_markup=to_markup(kb[offset : offset + 80])
        )


@router.message(Command("postmatch"))
async def menu(message: Message, services: Services, ctx: UserContext) -> None:
    items = await services.postmatch.list_surveys(ctx)
    buttons = [
        (f"Мой отчёт · {name}", f"psm:{sid}:period:{ctx.user.id}")
        for sid, name in await services.postmatch.reports.own_surveys(ctx)
    ]
    buttons.extend((name, f"psm:{sid}:card") for sid, name in items)
    await send_options(
        message,
        (
            "🏀 <b>Послематчевые опросы</b>\n\nВыберите матч или свой отчёт."
            if buttons
            else "🏀 <b>Послематчевые опросы</b>\n\nДоступных опросов нет."
        ),
        buttons,
    )


async def player_reply(
    message: Message, state: FSMContext, token: str, result: tuple[str, Keyboard | None, int]
) -> None:
    msg, kb, step = result
    await state.clear()
    if step in (PAIN_DETAIL, 7):
        await state.set_state(SurveyInput.text)
        await state.update_data(token=token, step=step)
    # Split only between complete HTML blocks; escaped free text stays intact.
    parts = []
    current = ""
    for block in msg.split("\n\n"):
        # Telegram limits rendered UTF-16 text, not the length of HTML entities.
        candidate = current + ("\n\n" if current else "") + block
        if current and len(unescape(candidate).encode("utf-16-le")) // 2 > 3500:
            parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)
    for index, part in enumerate(parts):
        sent = await message.answer(
            part, reply_markup=to_markup(kb) if index == len(parts) - 1 else None
        )
        if step in (PAIN_DETAIL, 7) and index == len(parts) - 1:
            await state.update_data(
                question_chat_id=sent.chat.id, question_message_id=sent.message_id
            )


def _missing_keyboard(exc: TelegramBadRequest) -> bool:
    return any(
        reason in exc.message.lower()
        for reason in (
            "message is not modified",
            "message to edit not found",
            "message can't be edited",
        )
    )


@router.callback_query(F.data.startswith("ps:"))
async def play(cb: CallbackQuery, state: FSMContext, services: Services, ctx: UserContext) -> None:
    msg = callback_message(cb)
    parts = (cb.data or "").split(":")
    if not msg or len(parts) != 4 or not parts[2].isdigit():
        raise Conflict("Кнопка недоступна.")
    _, token, step, action = parts
    result = await services.postmatch.player_action(ctx, token, int(step), action)
    await cb.answer()
    if int(step) in (5, 6) and result[2] == int(step):
        try:
            await msg.edit_reply_markup(reply_markup=to_markup(result[1]))
        except TelegramBadRequest as exc:
            error = exc.message.lower()
            if "message is not modified" in error:
                return
            if "message to edit not found" not in error and "message can't be edited" not in error:
                raise
            await player_reply(msg, state, token, result)
        return
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as exc:
        if not _missing_keyboard(exc):
            raise
    await player_reply(msg, state, token, result)


@router.message(SurveyInput.text, F.text)
async def free_text(msg: Message, state: FSMContext, services: Services, ctx: UserContext) -> None:
    data = await state.get_data()
    result = await services.postmatch.player_action(
        ctx, data["token"], data["step"], "text", msg.text
    )
    if data.get("question_chat_id") is not None and data.get("question_message_id") is not None:
        assert msg.bot is not None
        try:
            await msg.bot.edit_message_reply_markup(
                chat_id=data["question_chat_id"],
                message_id=data["question_message_id"],
                reply_markup=None,
            )
        except TelegramBadRequest as exc:
            if not _missing_keyboard(exc):
                raise
    await player_reply(msg, state, data["token"], result)


@router.callback_query(F.data.startswith("psm:"))
async def manage(
    cb: CallbackQuery, state: FSMContext, services: Services, ctx: UserContext
) -> None:
    msg = callback_message(cb)
    parts = (cb.data or "").split(":")
    if not msg or len(parts) < 3 or not parts[1].isdigit():
        raise Conflict("Кнопка недоступна.")
    sid = int(parts[1])
    action = parts[2]
    uid = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    service = services.postmatch
    card = ""
    players: list[tuple[int, str, str]] = []
    if action not in {"period", "csvperiod", "dates", "request", "players"}:
        card, players = await service.card(ctx, sid)
    prefix = f"psm:{sid}:"
    buttons = []
    if action == "card":
        buttons = [
            ("Добавить игрока", "add"),
            ("Убрать из опроса", "remove"),
            ("Отчёт команды", "period:0"),
            ("Отчёт игрока", "players"),
        ]
        if ctx.is_admin:
            buttons.extend(
                [
                    ("Повторить приглашение", "resend"),
                    ("Экспорт CSV", "csvpreview"),
                    ("Удаление данных игрока", "erase"),
                ]
            )
    elif action == "players":
        names = await service.reports.report_players(ctx, sid)
        card = "👤 <b>Отчёт игрока</b>\n\n" + (
            "Выберите игрока." if names else "За этот период участников нет."
        )
        buttons = [(name, f"period:{pid}") for pid, name in names.items()]
    elif action in ("add", "remove", "erase", "resend"):
        titles = {
            "add": "➕ Добавить игрока",
            "remove": "➖ Убрать из опроса",
            "erase": "🗑 Удаление данных игрока",
            "resend": "🔔 Повторить приглашение",
        }
        card = f"<b>{titles[action]}</b>\n\nВыберите игрока."
        for pid, name, status in players:
            if action == "add" and status not in ("new", "removed"):
                continue
            if action == "remove" and status in ("new", "removed"):
                continue
            if action == "resend" and status not in ("invited", "started"):
                continue
            op = {
                "resend": "send_again",
                "add": "join",
                "remove": "drop",
                "erase": "confirm_erase",
            }[action]
            buttons.append((name, f"{op}:{pid}"))
        if not buttons:
            card = f"<b>{titles[action]}</b>\n\nНет игроков для этого действия."
    elif action == "send_again":
        await service.resend(ctx, sid, uid)
        card = (
            "✅ <b>Приглашение поставлено на доставку</b>\n\n"
            "Бот отправит его игроку в личные сообщения."
        )
    elif action in ("join", "drop"):
        await service.change(ctx, sid, uid, action == "join")
        card, _ = await service.card(ctx, sid)
        notice = "Игрок добавлен" if action == "join" else "Игрок убран из опроса"
        card = f"✅ <b>{notice}</b>\n\n{card}"
        buttons = [("К матчу", "card")]
    elif action in ("preview", "csvpreview"):
        card = "📄 <b>Формирование отчёта</b>\n\n" + card
        buttons = [
            (
                "Подтвердить формирование",
                "request:0:match:" + ("csv" if action == "csvpreview" else "pdf"),
            )
        ]
    elif action in ("period", "csvperiod"):
        card = (
            "📅 <b>Период истории</b>\n\nВыберите период отчёта. По умолчанию — последние 5 матчей."
        )
        kind = "csv" if action == "csvperiod" else "pdf"
        buttons = [
            (label, f"request:{uid}:{period}:{kind}")
            for label, period in (
                ("Последние 5", "5"),
                ("Последние 10", "10"),
                ("Последние 20", "20"),
                ("Все", "all"),
            )
        ]
        buttons.append(("Диапазон дат", f"dates:{uid}:{kind}"))
        if ctx.is_admin and kind == "pdf":
            buttons.append(("Экспорт истории в CSV", f"csvperiod:{uid}"))
    elif action == "dates":
        await state.set_state(SurveyInput.dates)
        await state.update_data(sid=sid, uid=uid, kind=parts[4] if len(parts) == 5 else "pdf")
        card = (
            "📅 <b>Диапазон дат</b>\n\nВведите даты в формате <code>ГГГГ-ММ-ДД..ГГГГ-ММ-ДД</code>."
        )
    elif action == "request" and len(parts) == 6:
        card = await service.reports.request(ctx, sid, period=parts[4], player=uid, kind=parts[5])
    elif action == "confirm_erase" and ctx.is_admin:
        card = (
            "🗑 <b>Удаление данных игрока</b>\n\n"
            "По запросу игрока удалить его анкеты и связанные отчёты этого состава?"
        )
        buttons = [("Удалить", f"do_erase:{uid}"), ("Отмена", "card")]
    elif action == "do_erase":
        await service.erase(ctx, sid, uid)
        card = "✅ <b>Данные удалены.</b>"
    else:
        raise Conflict("Кнопка недоступна.")
    await cb.answer()
    await send_options(msg, card, [(label, prefix + value) for label, value in buttons])


@router.message(SurveyInput.dates, F.text)
async def dates(msg: Message, state: FSMContext, services: Services, ctx: UserContext) -> None:
    data = await state.get_data()
    reply = await services.postmatch.reports.request(
        ctx, data["sid"], period=(msg.text or "").strip(), player=data["uid"], kind=data["kind"]
    )
    await state.clear()
    await msg.answer(reply)
