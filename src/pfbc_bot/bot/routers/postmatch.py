"""Личная анкета и управление отчётами /postmatch."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from pfbc_bot.bot.helpers import callback_message
from pfbc_bot.domain.postmatch import PAIN_DETAIL, player_labels
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
        await message.answer("Продолжение списка", reply_markup=to_markup(kb[offset : offset + 80]))


@router.message(Command("postmatch"))
async def menu(message: Message, services: Services, ctx: UserContext) -> None:
    items = await services.postmatch.list_surveys(ctx)
    await send_options(
        message,
        "Послематчевые опросы" if items else "Доступных опросов нет.",
        [(name, f"psm:{sid}:card") for sid, name in items],
    )


async def player_reply(
    message: Message, state: FSMContext, token: str, result: tuple[str, Keyboard | None, int]
) -> None:
    msg, kb, step = result
    await state.clear()
    if step in (PAIN_DETAIL, 7):
        await state.set_state(SurveyInput.text)
        await state.update_data(token=token, step=step)
    await message.answer(msg, reply_markup=to_markup(kb))


@router.callback_query(F.data.startswith("ps:"))
async def play(cb: CallbackQuery, state: FSMContext, services: Services, ctx: UserContext) -> None:
    msg = callback_message(cb)
    parts = (cb.data or "").split(":")
    if not msg or len(parts) != 4 or not parts[2].isdigit():
        raise Conflict("Кнопка недоступна.")
    _, token, step, action = parts
    result = await services.postmatch.player_action(ctx, token, int(step), action)
    await cb.answer()
    await player_reply(msg, state, token, result)


@router.message(SurveyInput.text, F.text)
async def free_text(msg: Message, state: FSMContext, services: Services, ctx: UserContext) -> None:
    data = await state.get_data()
    result = await services.postmatch.player_action(
        ctx, data["token"], data["step"], "text", msg.text
    )
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
    card, players = await service.card(ctx, sid)
    prefix = f"psm:{sid}:"
    buttons = []
    if action == "card":
        buttons = [
            ("Добавить игрока", "add"),
            ("Убрать из опроса", "remove"),
            ("Отчёт за матч", "preview"),
            ("История команды", "period:0"),
            ("История игрока", "players"),
        ]
        if ctx.is_admin:
            buttons.extend(
                [
                    ("Повторить приглашение", "resend"),
                    ("Экспорт CSV", "csvpreview"),
                    ("Удаление данных игрока", "erase"),
                ]
            )
    elif action in ("add", "remove", "players", "erase", "resend"):
        card = "Выберите игрока"
        labels = player_labels({user_id: name for user_id, name, _ in players})
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
                "players": "period",
                "erase": "confirm_erase",
            }[action]
            if action == "players":
                name = labels[pid]
            buttons.append((f"{name} · {pid}", f"{op}:{pid}"))
    elif action == "send_again":
        await service.resend(ctx, sid, uid)
        card = "Повторное приглашение поставлено на доставку."
    elif action in ("join", "drop"):
        await service.change(ctx, sid, uid, action == "join")
        card, _ = await service.card(ctx, sid)
        buttons = [("К матчу", "card")]
    elif action in ("preview", "csvpreview"):
        buttons = [
            (
                "Подтвердить формирование",
                "request:0:match:" + ("csv" if action == "csvpreview" else "pdf"),
            )
        ]
    elif action in ("period", "csvperiod"):
        card = "Период истории (по умолчанию — последние 5 матчей)"
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
        card = "Введите даты в формате ГГГГ-ММ-ДД..ГГГГ-ММ-ДД."
    elif action == "request" and len(parts) == 6:
        card = await service.reports.request(ctx, sid, period=parts[4], player=uid, kind=parts[5])
    elif action == "confirm_erase" and ctx.is_admin:
        card = "По запросу игрока удалить его анкеты и связанные отчёты этого состава?"
        buttons = [("Удалить", f"do_erase:{uid}"), ("Отмена", "card")]
    elif action == "do_erase":
        await service.erase(ctx, sid, uid)
        card = "Данные удалены."
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
