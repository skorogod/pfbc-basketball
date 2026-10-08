"""Журнал событий (ФТ-9): /events, /event_add, /event_del."""

from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pfbc_bot.bot.helpers import ask_squad, available_squads, callback_message
from pfbc_bot.bot.keyboards.common import (
    Choice,
    ItemAction,
    SquadPick,
    choices_keyboard,
    skip_keyboard,
    yes_no_keyboard,
)
from pfbc_bot.bot.states import EventCreation
from pfbc_bot.domain.dates import localize, parse_local_datetime, to_local, utcnow
from pfbc_bot.domain.enums import EVENT_TYPE_TITLES, EventType, SquadRole
from pfbc_bot.schemas.events import EventCreate, ScheduleConflict
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.services.events import (
    EVENT_MANAGERS,
    ScheduleConflictError,
    format_conflicts,
    format_event_line,
)
from pfbc_bot.texts import ru

router = Router(name="events")
router.message.filter(F.chat.type == ChatType.PRIVATE)

ADD = "ev_add"
TYPE_FLOW, DURATION_FLOW, CONFLICT_FLOW = "ev_type", "ev_dur", "ev_conf"
PLACE_FLOW, DESCRIPTION_FLOW, PUBLISH_FLOW = "ev_place", "ev_descr", "ev_pub"
DURATION_OPTIONS = (60, 90, 120, 180)


@router.message(Command("events"))
async def cmd_events(message: Message, ctx: UserContext, services: Services) -> None:
    squad_ids = [s.id for s in await available_squads(ctx, services, *SquadRole)]
    events = await services.events.upcoming(squad_ids)
    if not events:
        await message.answer(ru.EVENTS_EMPTY)
        return
    lines = [ru.EVENTS_HEADER, *(format_event_line(e, name, tz) for e, name, tz in events)]
    await message.answer("\n".join(lines))


@router.message(Command("event_del"))
async def cmd_event_del(message: Message, ctx: UserContext, services: Services) -> None:
    squad_ids = [s.id for s in await available_squads(ctx, services, *EVENT_MANAGERS)]
    events = await services.events.upcoming(squad_ids)
    if not events:
        await message.answer(ru.EVENTS_EMPTY)
        return
    builder = InlineKeyboardBuilder()
    for event, squad_name, tz in events:
        when = to_local(event.starts_at, tz).strftime("%d.%m %H:%M")
        builder.button(
            text=f"🗑 {when} {event.title} ({squad_name})"[:60],
            callback_data=ItemAction(kind="ev", action="del", item_id=event.id),
        )
    builder.adjust(1)
    await message.answer(ru.EVENTS_HEADER, reply_markup=builder.as_markup())


@router.callback_query(ItemAction.filter((F.kind == "ev") & (F.action == "del")))
async def on_event_delete(
    callback: CallbackQuery, callback_data: ItemAction, ctx: UserContext, services: Services
) -> None:
    await services.events.delete(ctx, callback_data.item_id)
    await callback.answer(ru.EVENT_DELETED, show_alert=True)


# --- создание события ---
# Шаги: тип → название → начало → длительность → [проверка пересечений] → место → описание →
# публиковать ли в группе → создание. Пересечение — предупреждение, решение за пользователем.
@router.message(Command("event_add"))
async def cmd_event_add(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    if squad := await ask_squad(message, ctx, services, ADD, *EVENT_MANAGERS):
        await _start(message, squad, state)


@router.callback_query(SquadPick.filter(F.flow == ADD))
async def on_squad_picked(
    callback: CallbackQuery, callback_data: SquadPick, services: Services, state: FSMContext
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await _start(message, await services.squads.get(callback_data.squad_id), state)


async def _start(message: Message, squad: SquadDTO, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(squad_id=squad.id, tz=squad.timezone)
    await state.set_state(EventCreation.type)
    await message.answer(
        ru.EVENT_ASK_TYPE,
        reply_markup=choices_keyboard(
            TYPE_FLOW, [(t.value, title) for t, title in EVENT_TYPE_TITLES.items()]
        ),
    )


@router.callback_query(EventCreation.type, Choice.filter(F.flow == TYPE_FLOW))
async def on_type(callback: CallbackQuery, callback_data: Choice, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(type=EventType(callback_data.value).value)
    await state.set_state(EventCreation.title)
    if message := callback_message(callback):
        await message.answer(ru.EVENT_ASK_TITLE)


@router.message(EventCreation.title, F.text)
async def on_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=(message.text or "").strip()[:200])
    await _ask_when(message, state)


async def _ask_when(message: Message, state: FSMContext) -> None:
    await state.set_state(EventCreation.when)
    await message.answer(ru.EVENT_ASK_WHEN)


@router.message(EventCreation.when, F.text)
async def on_when(message: Message, state: FSMContext) -> None:
    tz = str((await state.get_data())["tz"])
    try:
        naive = parse_local_datetime(message.text or "", today=to_local(utcnow(), tz).date())
    except ValueError as exc:
        await message.answer(str(exc))
        return
    starts_at = localize(naive, tz)
    if starts_at <= utcnow():
        await message.answer(ru.PAST_DATE)
        return
    await state.update_data(starts_at=starts_at.isoformat())
    await state.set_state(EventCreation.duration)
    await message.answer(
        ru.EVENT_ASK_DURATION,
        reply_markup=choices_keyboard(
            DURATION_FLOW, [(str(m), f"{m} мин") for m in DURATION_OPTIONS], width=4
        ),
    )


@router.message(EventCreation.duration, F.text)
async def on_duration_text(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or not 15 <= int(text) <= 1440:
        await message.answer(ru.EVENT_BAD_DURATION)
        return
    await _set_duration(message, int(text), ctx, services, state)


@router.callback_query(EventCreation.duration, Choice.filter(F.flow == DURATION_FLOW))
async def on_duration_choice(
    callback: CallbackQuery,
    callback_data: Choice,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await _set_duration(message, int(callback_data.value), ctx, services, state)


async def _set_duration(
    message: Message, minutes: int, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    # время изменилось — прежнее подтверждение пересечений больше не действует
    await state.update_data(duration_minutes=minutes, conflicts_confirmed=False)
    data = await state.get_data()
    conflicts = await services.events.find_conflicts(
        ctx, int(data["squad_id"]), datetime.fromisoformat(data["starts_at"]), minutes
    )
    if conflicts:
        await _warn_conflicts(message, state, conflicts, str(data["tz"]), final=False)
    elif "publish" in data:  # время меняли на последнем шаге — остальное уже заполнено
        await _create(message, ctx, services, state)
    else:
        await _ask_place(message, state)


async def _warn_conflicts(
    message: Message,
    state: FSMContext,
    conflicts: list[ScheduleConflict],
    tz: str,
    *,
    final: bool,
) -> None:
    await state.set_state(EventCreation.conflict)
    proceed = ru.BTN_CREATE_ANYWAY if final else ru.BTN_CONTINUE
    await message.answer(
        f"{format_conflicts(conflicts, tz)}\n\n{ru.EVENT_CONFLICT_QUESTION}",
        reply_markup=choices_keyboard(
            CONFLICT_FLOW,
            [("go", proceed), ("change", ru.BTN_CHANGE_TIME), ("cancel", ru.BTN_CANCEL)],
            width=1,
        ),
    )


@router.callback_query(EventCreation.conflict, Choice.filter(F.flow == CONFLICT_FLOW))
async def on_conflict_choice(
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
    if callback_data.value == "cancel":
        await state.clear()
        await message.answer(ru.CANCELLED)
    elif callback_data.value == "change":
        await _ask_when(message, state)
    else:
        await state.update_data(conflicts_confirmed=True)
        if "publish" in await state.get_data():
            await _create(message, ctx, services, state)
        else:
            await _ask_place(message, state)


async def _ask_place(message: Message, state: FSMContext) -> None:
    await state.set_state(EventCreation.place)
    await message.answer(ru.EVENT_ASK_PLACE, reply_markup=skip_keyboard(PLACE_FLOW))


@router.message(EventCreation.place, F.text)
async def on_place(message: Message, state: FSMContext) -> None:
    await state.update_data(place=(message.text or "").strip()[:255])
    await _ask_description(message, state)


@router.callback_query(EventCreation.place, Choice.filter(F.flow == PLACE_FLOW))
async def on_place_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await _ask_description(message, state)


async def _ask_description(message: Message, state: FSMContext) -> None:
    await state.set_state(EventCreation.description)
    await message.answer(ru.EVENT_ASK_DESCRIPTION, reply_markup=skip_keyboard(DESCRIPTION_FLOW))


@router.message(EventCreation.description, F.text)
async def on_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=(message.text or "").strip()[:2000])
    await _ask_publish(message, state)


@router.callback_query(EventCreation.description, Choice.filter(F.flow == DESCRIPTION_FLOW))
async def on_description_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await _ask_publish(message, state)


async def _ask_publish(message: Message, state: FSMContext) -> None:
    await state.set_state(EventCreation.publish)
    await message.answer(ru.EVENT_ASK_PUBLISH, reply_markup=yes_no_keyboard(PUBLISH_FLOW))


@router.callback_query(EventCreation.publish, Choice.filter(F.flow == PUBLISH_FLOW))
async def on_publish(
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
    await state.update_data(publish=callback_data.value == "yes")
    await _create(message, ctx, services, state)


async def _create(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    data = await state.get_data()
    # состояние — до создания: повторный шаг (двойное нажатие) не создаст второе событие
    await state.clear()
    try:
        event = await services.events.create(
            ctx,
            EventCreate(
                squad_id=data["squad_id"],
                type=EventType(data["type"]),
                title=data["title"],
                starts_at=data["starts_at"],
                duration_minutes=data["duration_minutes"],
                place=data.get("place"),
                description=data.get("description"),
                notify_group=bool(data.get("publish")),
            ),
            confirm_conflicts=bool(data.get("conflicts_confirmed")),
        )
    except ScheduleConflictError as exc:
        # пока заполняли форму, появилось новое пересечение — спрашиваем ещё раз
        await state.set_data(data)
        await _warn_conflicts(message, state, exc.conflicts, exc.tz, final=True)
        return
    squad = await services.squads.get(event.squad_id)
    text = ru.EVENT_CREATED.format(event=format_event_line(event, squad.name, squad.timezone))
    if event.notify_group:
        text += f"\n{ru.EVENT_PUBLISHED}"
    await message.answer(text)
