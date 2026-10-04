"""Расписание и тренировки (ФТ-2): /schedule, /trainings, /training_add, /attendance."""

import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pfbc_bot.bot.helpers import ask_squad, callback_message
from pfbc_bot.bot.keyboards.common import ItemAction, SquadPick
from pfbc_bot.bot.states import ScheduleEdit, TrainingEdit
from pfbc_bot.domain.dates import (
    WEEKDAY_SHORT,
    format_local,
    localize,
    parse_local_datetime,
    parse_schedule_line,
    to_local,
    utcnow,
)
from pfbc_bot.domain.enums import SquadRole
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.schemas.trainings import ScheduleCreate, TrainingCreate
from pfbc_bot.services.container import Services
from pfbc_bot.texts import ru

router = Router(name="trainings")
router.message.filter(F.chat.type == ChatType.PRIVATE)

SCHEDULE, TRAININGS, TRAINING_ADD, ATTENDANCE = "schedule", "trainings", "tr_add", "attendance"
COACH = (SquadRole.COACH,)
STAFF = (SquadRole.COACH, SquadRole.CAPTAIN)


# --- вход в сценарии: команда или выбор состава ---
@router.message(Command("schedule"))
async def cmd_schedule(message: Message, ctx: UserContext, services: Services) -> None:
    if squad := await ask_squad(message, ctx, services, SCHEDULE, *COACH):
        await show_schedule(message, squad, services)


@router.message(Command("trainings"))
async def cmd_trainings(message: Message, ctx: UserContext, services: Services) -> None:
    if squad := await ask_squad(message, ctx, services, TRAININGS, *COACH):
        await show_trainings(message, squad, services)


@router.message(Command("training_add"))
async def cmd_training_add(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    if squad := await ask_squad(message, ctx, services, TRAINING_ADD, *COACH):
        await start_training_add(message, squad, state)


@router.message(Command("attendance"))
async def cmd_attendance(message: Message, ctx: UserContext, services: Services) -> None:
    if squad := await ask_squad(message, ctx, services, ATTENDANCE, *STAFF):
        await show_attendance_choice(message, squad, services)


@router.callback_query(
    SquadPick.filter(F.flow.in_({SCHEDULE, TRAININGS, TRAINING_ADD, ATTENDANCE}))
)
async def on_squad_picked(
    callback: CallbackQuery, callback_data: SquadPick, services: Services, state: FSMContext
) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    squad = await services.squads.get(callback_data.squad_id)
    handlers = {
        SCHEDULE: lambda: show_schedule(message, squad, services),
        TRAININGS: lambda: show_trainings(message, squad, services),
        TRAINING_ADD: lambda: start_training_add(message, squad, state),
        ATTENDANCE: lambda: show_attendance_choice(message, squad, services),
    }
    await handlers[callback_data.flow]()


# --- расписание ---
async def show_schedule(message: Message, squad: SquadDTO, services: Services) -> None:
    schedules = await services.trainings.list_schedules(squad.id)
    builder = InlineKeyboardBuilder()
    lines = [ru.SCHEDULE_HEADER.format(squad=html.escape(squad.name))]
    for s in schedules:
        day = WEEKDAY_SHORT[s.weekday]
        label = f"{day} {s.start_time:%H:%M} · {s.duration_minutes} мин · {s.venue}"
        lines.append(f"• {html.escape(label)}")
        delete = ItemAction(kind="sch", action="del", item_id=s.id)
        builder.button(text=f"🗑 {label}"[:60], callback_data=delete)
    if not schedules:
        lines.append(ru.SCHEDULE_EMPTY)
    builder.button(
        text="➕ Добавить", callback_data=ItemAction(kind="sch", action="add", item_id=squad.id)
    )
    builder.adjust(1)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(ItemAction.filter(F.kind == "sch"))
async def on_schedule_action(
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
    if callback_data.action == "add":
        await state.set_state(ScheduleEdit.line)
        await state.update_data(squad_id=callback_data.item_id)
        await message.answer(ru.SCHEDULE_ADD_PROMPT)
    elif callback_data.action == "del":
        await services.trainings.remove_schedule(ctx, callback_data.item_id)
        await message.answer(ru.SCHEDULE_REMOVED)


@router.message(ScheduleEdit.line, F.text)
async def on_schedule_line(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    try:
        line = parse_schedule_line(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    squad_id = int((await state.get_data())["squad_id"])
    await services.trainings.add_schedule(
        ctx,
        ScheduleCreate(
            squad_id=squad_id,
            weekday=line.weekday,
            start_time=line.start_time,
            duration_minutes=line.duration_minutes,
            venue=line.venue,
        ),
    )
    await state.clear()
    await message.answer(ru.SCHEDULE_ADDED)
    await show_schedule(message, await services.squads.get(squad_id), services)


# --- ближайшие тренировки: отмена и перенос ---
async def show_trainings(message: Message, squad: SquadDTO, services: Services) -> None:
    trainings = await services.trainings.upcoming(squad.id)
    if not trainings:
        await message.answer(ru.TRAININGS_EMPTY)
        return
    builder = InlineKeyboardBuilder()
    lines = [ru.TRAININGS_HEADER.format(squad=html.escape(squad.name))]
    for t in trainings:
        when = format_local(t.starts_at, squad.timezone)
        lines.append(f"• {when} — {html.escape(t.venue)}")
        builder.button(
            text=f"❌ {when}", callback_data=ItemAction(kind="tr", action="cancel", item_id=t.id)
        )
        builder.button(
            text="🔁 Перенести", callback_data=ItemAction(kind="tr", action="move", item_id=t.id)
        )
    builder.adjust(2)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(ItemAction.filter((F.kind == "tr") & F.action.in_({"cancel", "move"})))
async def on_training_action(
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
    if callback_data.action == "cancel":
        await services.trainings.cancel(ctx, callback_data.item_id)
        await message.answer(ru.TRAINING_DONE)
    else:
        await state.set_state(TrainingEdit.move)
        await state.update_data(training_id=callback_data.item_id)
        await message.answer(ru.TRAINING_MOVE_PROMPT)


@router.message(TrainingEdit.move, F.text)
async def on_training_move(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    training = await services.trainings.get(int((await state.get_data())["training_id"]))
    squad = await services.squads.get(training.squad_id)
    try:
        naive = parse_local_datetime(
            message.text or "", today=to_local(utcnow(), squad.timezone).date()
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await services.trainings.move(ctx, training.id, localize(naive, squad.timezone))
    await state.clear()
    await message.answer(ru.TRAINING_DONE)


# --- разовая тренировка ---
async def start_training_add(message: Message, squad: SquadDTO, state: FSMContext) -> None:
    await state.set_state(TrainingEdit.add)
    await state.update_data(squad_id=squad.id)
    await message.answer(ru.TRAINING_ADD_PROMPT)


@router.message(TrainingEdit.add, F.text)
async def on_training_add(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    squad = await services.squads.get(int((await state.get_data())["squad_id"]))
    parts = (message.text or "").split(maxsplit=3)
    try:
        if len(parts) < 4 or not parts[2].isdigit():
            raise ValueError(ru.TRAINING_ADD_PROMPT)
        naive = parse_local_datetime(
            f"{parts[0]} {parts[1]}", today=to_local(utcnow(), squad.timezone).date()
        )
        data = TrainingCreate(
            squad_id=squad.id,
            starts_at=localize(naive, squad.timezone),
            duration_minutes=int(parts[2]),
            venue=parts[3],
        )
    except ValueError as exc:  # pydantic.ValidationError — подкласс ValueError
        await message.answer(str(exc))
        return
    training = await services.trainings.add_one_off(ctx, data)
    await state.clear()
    await message.answer(
        ru.TRAINING_ADDED.format(
            when=format_local(training.starts_at, squad.timezone), venue=html.escape(training.venue)
        )
    )


# --- посещаемость ---
async def show_attendance_choice(message: Message, squad: SquadDTO, services: Services) -> None:
    trainings = await services.trainings.recent_and_upcoming(squad.id)
    if not trainings:
        await message.answer(ru.TRAININGS_EMPTY)
        return
    builder = InlineKeyboardBuilder()
    for t in trainings:
        builder.button(
            text=format_local(t.starts_at, squad.timezone),
            callback_data=ItemAction(kind="tr", action="att", item_id=t.id),
        )
    builder.adjust(1)
    await message.answer(ru.CHOOSE_TRAINING, reply_markup=builder.as_markup())


@router.callback_query(ItemAction.filter((F.kind == "tr") & (F.action == "att")))
async def on_attendance(
    callback: CallbackQuery, callback_data: ItemAction, ctx: UserContext, services: Services
) -> None:
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    summary = await services.polls.attendance(ctx, callback_data.item_id)
    squad = await services.squads.get(summary.training.squad_id)
    lines = "\n".join(
        f"<b>{html.escape(option)}</b> ({len(names)}): {', '.join(names) or '—'}"
        for option, names in summary.by_option.items()
    )
    await message.answer(
        ru.ATTENDANCE.format(
            when=format_local(summary.training.starts_at, squad.timezone),
            lines=lines,
            missing=", ".join(summary.not_answered) or "—",
        )
    )
