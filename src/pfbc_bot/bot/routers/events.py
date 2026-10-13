"""Журнал событий (ФТ-9): /events, /event_add, /event_del, /review, /tournaments."""

import html
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pfbc_bot import callbacks
from pfbc_bot.bot.helpers import (
    ask_squad,
    available_squads,
    callback_message,
    dismiss,
    picked_squad,
)
from pfbc_bot.bot.keyboards.common import (
    Choice,
    ItemAction,
    SquadPick,
    choices_keyboard,
    skip_keyboard,
    yes_no_keyboard,
)
from pfbc_bot.bot.states import (
    EventCreation,
    EventMove,
    ReviewTime,
    TournamentEdit,
    TournamentPrice,
)
from pfbc_bot.domain.dates import (
    format_interval,
    format_local,
    local_to_utc,
    localize,
    parse_date,
    parse_local_datetime,
    to_local,
    utcnow,
)
from pfbc_bot.domain.enums import EVENT_TYPE_TITLES, EventType
from pfbc_bot.domain.payments import format_amount, parse_amount
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.schemas.events import EventCreate, EventDTO, ScheduleConflict, TournamentCard
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.services.events import (
    DIVISION_MAX,
    ScheduleConflictError,
    event_permission,
    format_conflicts,
    format_event_line,
    format_short,
    format_tournament_period,
    format_when,
)
from pfbc_bot.texts import ru

router = Router(name="events")
router.message.filter(F.chat.type == ChatType.PRIVATE)

ADD, REVIEW_ADD = "ev_add", "ev_review"
TYPE_FLOW, DURATION_FLOW, CONFLICT_FLOW = "ev_type", "ev_dur", "ev_conf"
TITLE_FLOW, WHEN_FLOW, TOURNAMENT_FLOW = "ev_title", "ev_when", "ev_trn"
TOURNAMENT_START_FLOW, TOURNAMENT_LENGTH_FLOW = "ev_tstart", "ev_tlen"
GAME_PRICE_FLOW, DIVISION_FLOW = "ev_price", "ev_div"
PLACE_FLOW, DESCRIPTION_FLOW, PUBLISH_FLOW = "ev_place", "ev_descr", "ev_pub"
DURATION_OPTIONS = (60, 90, 120, 180)


# создать или удалить событие можно с любым из прав (тип ограничивается отдельно)
EVENT_PERMISSIONS = (Permission.MEETINGS, Permission.EVENTS)


class TournamentLink(CallbackData, prefix="tl"):
    """Привязать (link=1) или отвязать (link=0) матч от турнира."""

    tournament_id: int
    match_id: int
    link: int


@router.message(Command("events"))
async def cmd_events(message: Message, ctx: UserContext, services: Services) -> None:
    squad_ids = [s.id for s in await available_squads(ctx, services)]
    events = await services.events.upcoming(squad_ids)
    if not events:
        await message.answer(ru.EVENTS_EMPTY)
        return
    lines = [ru.EVENTS_HEADER, *(format_event_line(e, name, tz) for e, name, tz in events)]
    await message.answer("\n".join(lines))


@router.message(Command("event_del"))
async def cmd_event_del(message: Message, ctx: UserContext, services: Services) -> None:
    events = await _manageable_events(ctx, services)  # только те, что можно удалить
    if not events:
        await message.answer(ru.EVENTS_EMPTY)
        return
    builder = InlineKeyboardBuilder()
    for event, squad_name, tz in events:
        when = format_short(event, tz)
        builder.button(
            text=f"🗑 {when} {event.title} ({squad_name})"[:60],
            callback_data=ItemAction(kind="ev", action="del", item_id=event.id),
        )
    builder.adjust(1)
    await message.answer(ru.EVENTS_HEADER, reply_markup=builder.as_markup())


@router.message(Command("event_move"))
async def cmd_event_move(message: Message, ctx: UserContext, services: Services) -> None:
    events = await _manageable_events(ctx, services)
    if not events:
        await message.answer(ru.EVENTS_EMPTY)
        return
    builder = InlineKeyboardBuilder()
    for event, squad_name, tz in events:
        builder.button(
            text=f"🔁 {format_short(event, tz)} {event.title} ({squad_name})"[:60],
            callback_data=ItemAction(kind="ev", action="move", item_id=event.id),
        )
    builder.adjust(1)
    await message.answer(ru.EVENT_MOVE_CHOOSE, reply_markup=builder.as_markup())


@router.callback_query(ItemAction.filter((F.kind == "ev") & (F.action == "move")))
async def on_event_move_pick(
    callback: CallbackQuery,
    callback_data: ItemAction,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    event = await services.events.get(callback_data.item_id)
    if not ctx.can(event.squad_id, event_permission(event.type)):
        await callback.answer(ru.NO_PERMISSION, show_alert=True)
        return
    await callback.answer()
    squad = await services.squads.get(event.squad_id)
    await state.set_state(EventMove.when)
    await state.update_data(event_id=event.id, tz=squad.timezone, type=event.type.value)
    if message := callback_message(callback):
        tournament = event.type == EventType.TOURNAMENT
        await message.answer(ru.EVENT_MOVE_ASK_DATE if tournament else ru.EVENT_MOVE_ASK_WHEN)


@router.message(EventMove.when, F.text)
async def on_event_move_when(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    data = await state.get_data()
    tz = str(data["tz"])
    try:
        if data["type"] == EventType.TOURNAMENT.value:
            starts_at = local_to_utc(parse_date(message.text or ""), time(0, 0), tz)
        else:
            naive = parse_local_datetime(message.text or "", today=to_local(utcnow(), tz).date())
            starts_at = localize(naive, tz)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    event = await services.events.move(ctx, int(data["event_id"]), starts_at)
    text = ru.EVENT_MOVED.format(when=format_when(event, tz))
    if event.notify_group:
        text += "\n" + publication_note(event)
    await message.answer(text)


async def _manageable_events(
    ctx: UserContext, services: Services
) -> list[tuple[EventDTO, str, str]]:
    """Ближайшие события, которыми пользователь может управлять (по праву на тип)."""
    squad_ids = [s.id for s in await available_squads(ctx, services, *EVENT_PERMISSIONS)]
    return [
        item
        for item in await services.events.upcoming(squad_ids)
        if ctx.can(item[0].squad_id, event_permission(item[0].type))
    ]


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
    if squad := await ask_squad(message, ctx, services, ADD, *EVENT_PERMISSIONS):
        await _start(message, squad, ctx, services, state)


@router.message(Command("review"))
async def cmd_review(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    if squad := await ask_squad(message, ctx, services, REVIEW_ADD, Permission.EVENTS):
        await _start_review(message, squad, state)


@router.callback_query(SquadPick.filter(F.flow.in_({ADD, REVIEW_ADD})))
async def on_squad_picked(
    callback: CallbackQuery,
    callback_data: SquadPick,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    permissions = (Permission.EVENTS,) if callback_data.flow == REVIEW_ADD else EVENT_PERMISSIONS
    squad = await picked_squad(ctx, services, callback_data.squad_id, *permissions)
    await callback.answer()
    if message := callback_message(callback):
        if callback_data.flow == REVIEW_ADD:
            await _start_review(message, squad, state)
        else:
            await _start(message, squad, ctx, services, state)


async def _start(
    message: Message, squad: SquadDTO, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    """Шаг выбора типа — у всех одинаковый; кнопки — только разрешённые в составе типы
    (капитану — «Собрание»)."""
    await state.clear()
    await state.update_data(squad_id=squad.id, tz=squad.timezone)
    allowed = [t for t in EVENT_TYPE_TITLES if ctx.can(squad.id, event_permission(t))]
    if not allowed:
        await message.answer(ru.NO_PERMISSION)
        return
    await state.set_state(EventCreation.type)
    await message.answer(
        ru.EVENT_ASK_TYPE,
        reply_markup=choices_keyboard(
            TYPE_FLOW, [(t.value, EVENT_TYPE_TITLES[t]) for t in allowed]
        ),
    )


async def _start_review(message: Message, squad: SquadDTO, state: FSMContext) -> None:
    """Разбор игры — тот же сценарий события, но тип уже выбран."""
    await state.clear()
    await state.update_data(squad_id=squad.id, tz=squad.timezone, type=EventType.REVIEW.value)
    await _ask_title(message, state, EventType.REVIEW)


@router.callback_query(EventCreation.type, Choice.filter(F.flow == TYPE_FLOW))
async def on_type(
    callback: CallbackQuery,
    callback_data: Choice,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    event_type = EventType(callback_data.value)
    squad_id = int((await state.get_data())["squad_id"])
    if not ctx.can(squad_id, event_permission(event_type)):
        await callback.answer(ru.NO_PERMISSION, show_alert=True)
        return
    await callback.answer()
    message = callback_message(callback)
    if message is not None:
        await _choose_type(message, ctx, services, state, event_type)


async def _choose_type(
    message: Message,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
    event_type: EventType,
) -> None:
    await state.update_data(type=event_type.value)
    if event_type == EventType.MATCH:
        await _ask_tournament(message, ctx, services, state)
    else:
        await _ask_title(message, state, event_type)


async def _ask_tournament(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    """Матч: сначала турнир (если у состава есть турниры), потом данные матча."""
    data = await state.get_data()
    tournaments = await services.events.squad_tournaments(ctx, int(data["squad_id"]))
    if not tournaments:
        await _ask_title(message, state, EventType.MATCH)
        return
    await state.set_state(EventCreation.tournament)
    tz = str(data["tz"])
    options = [(str(t.id), f"🏆 {t.title} · {format_short(t, tz)}"[:60]) for t in tournaments]
    await message.answer(
        ru.MATCH_ASK_TOURNAMENT,
        reply_markup=choices_keyboard(
            TOURNAMENT_FLOW, [*options, ("none", ru.MATCH_BTN_NO_TOURNAMENT)], width=1
        ),
    )


@router.callback_query(EventCreation.tournament, Choice.filter(F.flow == TOURNAMENT_FLOW))
async def on_match_tournament(
    callback: CallbackQuery, callback_data: Choice, state: FSMContext
) -> None:
    await callback.answer()
    tournament_id = None if callback_data.value == "none" else int(callback_data.value)
    await state.update_data(tournament_id=tournament_id)
    if message := callback_message(callback):
        await message.edit_reply_markup(reply_markup=None)
        await _ask_title(message, state, EventType.MATCH)


async def _ask_title(message: Message, state: FSMContext, event_type: EventType) -> None:
    await state.set_state(EventCreation.title)
    markup = None
    if event_type == EventType.REVIEW:
        markup = choices_keyboard(TITLE_FLOW, [("default", ru.REVIEW_TITLE_DEFAULT)])
    await message.answer(ru.EVENT_ASK_TITLE, reply_markup=markup)


@router.message(EventCreation.title, F.text)
async def on_title(message: Message, services: Services, state: FSMContext) -> None:
    await state.update_data(title=(message.text or "").strip()[:200])
    await _ask_when(message, services, state)


@router.callback_query(EventCreation.title, Choice.filter(F.flow == TITLE_FLOW))
async def on_title_default(callback: CallbackQuery, services: Services, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(title=ru.REVIEW_TITLE_DEFAULT)
    if message := callback_message(callback):
        await _ask_when(message, services, state)


async def _ask_when(message: Message, services: Services, state: FSMContext) -> None:
    """Для разбора игры — кнопка со временем по умолчанию (за час до первой тренировки недели)."""
    data = await state.get_data()
    if data.get("type") == EventType.TOURNAMENT.value:
        await _ask_tournament_start(message, state)
        return
    await state.set_state(EventCreation.when)
    if data.get("type") != EventType.REVIEW.value:
        await message.answer(ru.EVENT_ASK_WHEN)
        return
    default = await services.events.review_default_at(int(data["squad_id"]))
    if default is None:
        await message.answer(ru.EVENT_ASK_WHEN_REVIEW_NO_DEFAULT)
        return
    await state.update_data(review_default=default.isoformat())
    button = ru.EVENT_BTN_REVIEW_DEFAULT.format(when=format_local(default, str(data["tz"])))
    await message.answer(
        ru.EVENT_ASK_WHEN_REVIEW, reply_markup=choices_keyboard(WHEN_FLOW, [("default", button)])
    )


@router.message(EventCreation.when, F.text)
async def on_when(message: Message, state: FSMContext) -> None:
    tz = str((await state.get_data())["tz"])
    try:
        naive = parse_local_datetime(message.text or "", today=to_local(utcnow(), tz).date())
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await _set_when(message, localize(naive, tz), state)


@router.callback_query(EventCreation.when, Choice.filter(F.flow == WHEN_FLOW))
async def on_when_default(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    message = callback_message(callback)
    default = (await state.get_data()).get("review_default")
    if message is None or default is None:
        return
    await message.edit_reply_markup(reply_markup=None)
    await _set_when(message, datetime.fromisoformat(default), state)


async def _set_when(message: Message, starts_at: datetime, state: FSMContext) -> None:
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


# --- турнир: дата старта (может быть неизвестна) и длительность в днях или сезон ---
async def _ask_tournament_start(message: Message, state: FSMContext) -> None:
    await state.set_state(EventCreation.tournament_start)
    await message.answer(
        ru.TOURNAMENT_ASK_START,
        reply_markup=choices_keyboard(
            TOURNAMENT_START_FLOW, [("unknown", ru.TOURNAMENT_BTN_START_UNKNOWN)]
        ),
    )


@router.message(EventCreation.tournament_start, F.text)
async def on_tournament_start(message: Message, state: FSMContext) -> None:
    try:
        day = parse_date(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    tz = str((await state.get_data())["tz"])
    # сезон может уже идти — дата старта в прошлом допустима
    await state.update_data(starts_at=local_to_utc(day, time(0, 0), tz).isoformat())
    await _ask_tournament_length(message, state)


@router.callback_query(
    EventCreation.tournament_start, Choice.filter(F.flow == TOURNAMENT_START_FLOW)
)
async def on_tournament_start_unknown(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(starts_at=None)
    if message := callback_message(callback):
        await message.edit_reply_markup(reply_markup=None)
        await _ask_tournament_length(message, state)


async def _ask_tournament_length(message: Message, state: FSMContext) -> None:
    await state.set_state(EventCreation.tournament_length)
    await message.answer(
        ru.TOURNAMENT_ASK_LENGTH,
        reply_markup=choices_keyboard(
            TOURNAMENT_LENGTH_FLOW, [("season", ru.TOURNAMENT_BTN_SEASON)]
        ),
    )


@router.message(EventCreation.tournament_length, F.text)
async def on_tournament_days(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or not 1 <= int(text) <= 366:
        await message.answer(ru.TOURNAMENT_BAD_LENGTH)
        return
    await state.update_data(duration_days=int(text), is_season=False)
    await _ask_game_price(message, state)


@router.callback_query(
    EventCreation.tournament_length, Choice.filter(F.flow == TOURNAMENT_LENGTH_FLOW)
)
async def on_tournament_season(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(duration_days=None, is_season=True)
    if message := callback_message(callback):
        await message.edit_reply_markup(reply_markup=None)
        await _ask_game_price(message, state)


async def _ask_game_price(message: Message, state: FSMContext) -> None:
    await state.set_state(EventCreation.game_price)
    await message.answer(ru.TOURNAMENT_ASK_GAME_PRICE, reply_markup=skip_keyboard(GAME_PRICE_FLOW))


@router.message(EventCreation.game_price, F.text)
async def on_game_price(message: Message, state: FSMContext) -> None:
    try:
        price = parse_amount(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(game_price=str(price))
    await _ask_division(message, state)


@router.callback_query(EventCreation.game_price, Choice.filter(F.flow == GAME_PRICE_FLOW))
async def on_game_price_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await message.edit_reply_markup(reply_markup=None)
        await _ask_division(message, state)


async def _ask_division(message: Message, state: FSMContext) -> None:
    await state.set_state(EventCreation.division)
    await message.answer(ru.TOURNAMENT_ASK_DIVISION, reply_markup=skip_keyboard(DIVISION_FLOW))


@router.message(EventCreation.division, F.text)
async def on_division(message: Message, state: FSMContext) -> None:
    division = (message.text or "").strip()
    if len(division) > DIVISION_MAX:
        await message.answer(ru.TOURNAMENT_BAD_DIVISION)
        return
    await state.update_data(division=division or None)
    await _ask_place(message, state)


@router.callback_query(EventCreation.division, Choice.filter(F.flow == DIVISION_FLOW))
async def on_division_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await message.edit_reply_markup(reply_markup=None)
        await _ask_place(message, state)


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
    if callback_data.value == "cancel":
        await state.clear()
        await dismiss(message)
        return
    await message.edit_reply_markup(reply_markup=None)
    if callback_data.value == "change":
        await _ask_when(message, services, state)
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
    """Последний шаг: сводка введённого — проверить перед созданием (ошибку исправит /cancel
    и новый ввод), затем вопрос о публикации."""
    await state.set_state(EventCreation.publish)
    summary = _summary(await state.get_data())
    await message.answer(
        f"{summary}\n\n{ru.EVENT_ASK_PUBLISH}", reply_markup=yes_no_keyboard(PUBLISH_FLOW)
    )


def _summary(data: dict[str, object]) -> str:
    tz = str(data["tz"])
    event_type = EventType(str(data["type"]))
    starts_at = data.get("starts_at")
    start = datetime.fromisoformat(str(starts_at)) if starts_at else None
    if event_type == EventType.TOURNAMENT:
        days = data.get("duration_days")
        when = format_tournament_period(
            start, int(str(days)) if days else None, bool(data.get("is_season")), tz
        )
    elif start is not None:
        minutes = int(str(data.get("duration_minutes", 90)))
        when = format_interval(start, start + timedelta(minutes=minutes), tz)
    else:
        when = ru.NOT_SET
    lines = [
        ru.EVENT_SUMMARY.format(
            type=EVENT_TYPE_TITLES[event_type], title=html.escape(str(data["title"])), when=when
        )
    ]
    if data.get("division"):
        lines.append(f"🏷 {html.escape(str(data['division']))}")
    if data.get("place"):
        lines.append(f"📍 {html.escape(str(data['place']))}")
    if data.get("description"):
        lines.append(html.escape(str(data["description"])))
    return "\n".join(lines)


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
                starts_at=data.get("starts_at"),
                duration_minutes=data.get("duration_minutes", 90),
                duration_days=data.get("duration_days"),
                is_season=bool(data.get("is_season")),
                tournament_id=data.get("tournament_id"),
                game_price=Decimal(data["game_price"]) if data.get("game_price") else None,
                division=data.get("division"),
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
        text += "\n" + publication_note(event)
    await message.answer(text)


# --- турниры: привязка матчей (админ) ---
@router.message(Command("tournaments"))
async def cmd_tournaments(message: Message, ctx: UserContext, services: Services) -> None:
    tournaments = await services.events.tournaments(ctx)
    if not tournaments:
        await message.answer(ru.TOURNAMENTS_EMPTY)
        return
    builder = InlineKeyboardBuilder()
    for tournament, squad_name, tz in tournaments:
        when = format_short(tournament, tz)
        builder.button(
            text=f"🏆 {tournament.title} · {when} ({squad_name})"[:60],
            callback_data=ItemAction(kind="trn", action="open", item_id=tournament.id),
        )
    builder.adjust(1)
    await message.answer(ru.TOURNAMENTS_HEADER, reply_markup=builder.as_markup())


@router.callback_query(ItemAction.filter((F.kind == "trn") & (F.action == "open")))
async def on_tournament_open(
    callback: CallbackQuery, callback_data: ItemAction, ctx: UserContext, services: Services
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        card = await services.events.tournament_card(ctx, callback_data.item_id)
        await message.answer(_tournament_text(card), reply_markup=_tournament_markup(card))


@router.callback_query(TournamentLink.filter())
async def on_tournament_link(
    callback: CallbackQuery, callback_data: TournamentLink, ctx: UserContext, services: Services
) -> None:
    await services.events.link_match(
        ctx, callback_data.tournament_id, callback_data.match_id, linked=bool(callback_data.link)
    )
    await callback.answer()
    if message := callback_message(callback):
        card = await services.events.tournament_card(ctx, callback_data.tournament_id)
        await message.edit_text(_tournament_text(card), reply_markup=_tournament_markup(card))


def _tournament_text(card: TournamentCard) -> str:
    price = card.tournament.game_price
    text = ru.TOURNAMENT_CARD.format(
        title=html.escape(card.tournament.title),
        squad=html.escape(card.squad_name),
        when=format_when(card.tournament, card.tz),
        division=html.escape(card.tournament.division or ru.NOT_SET),
        price=format_amount(price) if price is not None else ru.NOT_SET,
    )
    return text if card.matches else text + ru.TOURNAMENT_NO_MATCHES


def _tournament_markup(card: TournamentCard) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for text, action in (
        (ru.TOURNAMENT_BTN_TITLE, "title"),
        (ru.TOURNAMENT_BTN_DIVISION, "division"),
        (ru.TOURNAMENT_BTN_GAME_PRICE, "price"),
    ):
        builder.button(
            text=text,
            callback_data=ItemAction(kind="trn", action=action, item_id=card.tournament.id),
        )
    for match in card.matches:
        linked = match.tournament_id == card.tournament.id
        when = format_short(match, card.tz)
        builder.button(
            text=f"{'✅' if linked else '▫️'} {when} {match.title}"[:60],
            callback_data=TournamentLink(
                tournament_id=card.tournament.id, match_id=match.id, link=0 if linked else 1
            ),
        )
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(ItemAction.filter((F.kind == "trn") & (F.action == "price")))
async def on_tournament_price(
    callback: CallbackQuery, callback_data: ItemAction, state: FSMContext
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await state.set_state(TournamentPrice.value)
        await state.update_data(tournament_id=callback_data.item_id)
        await message.answer(
            ru.TOURNAMENT_ASK_GAME_PRICE_EDIT,
            reply_markup=choices_keyboard(
                GAME_PRICE_FLOW, [("clear", ru.TOURNAMENT_BTN_CLEAR_PRICE)]
            ),
        )


@router.message(TournamentPrice.value, F.text)
async def on_tournament_price_value(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    try:
        price = parse_amount(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await _save_price(message, ctx, services, state, price)


@router.callback_query(TournamentPrice.value, Choice.filter(F.flow == GAME_PRICE_FLOW))
async def on_tournament_price_clear(
    callback: CallbackQuery, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await message.edit_reply_markup(reply_markup=None)
        await _save_price(message, ctx, services, state, None)


async def _save_price(
    message: Message,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
    price: Decimal | None,
) -> None:
    tournament_id = int((await state.get_data())["tournament_id"])
    await state.clear()
    await services.events.set_game_price(ctx, tournament_id, price)
    await _show_tournament(message, ctx, services, tournament_id)


async def _show_tournament(
    message: Message, ctx: UserContext, services: Services, tournament_id: int
) -> None:
    card = await services.events.tournament_card(ctx, tournament_id)
    await message.answer(_tournament_text(card), reply_markup=_tournament_markup(card))


@router.callback_query(ItemAction.filter((F.kind == "trn") & (F.action == "title")))
async def on_tournament_title(
    callback: CallbackQuery, callback_data: ItemAction, state: FSMContext
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await state.set_state(TournamentEdit.title)
        await state.update_data(tournament_id=callback_data.item_id)
        await message.answer(ru.TOURNAMENT_ASK_TITLE_EDIT)


@router.message(TournamentEdit.title, F.text)
async def on_tournament_title_value(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    tournament_id = int((await state.get_data())["tournament_id"])
    await services.events.rename_tournament(ctx, tournament_id, message.text or "")
    await state.clear()
    await _show_tournament(message, ctx, services, tournament_id)


@router.callback_query(ItemAction.filter((F.kind == "trn") & (F.action == "division")))
async def on_tournament_division(
    callback: CallbackQuery, callback_data: ItemAction, state: FSMContext
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await state.set_state(TournamentEdit.division)
        await state.update_data(tournament_id=callback_data.item_id)
        await message.answer(
            ru.TOURNAMENT_ASK_DIVISION_EDIT,
            reply_markup=choices_keyboard(
                DIVISION_FLOW, [("clear", ru.TOURNAMENT_BTN_CLEAR_DIVISION)]
            ),
        )


@router.message(TournamentEdit.division, F.text)
async def on_tournament_division_value(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    await _save_division(message, ctx, services, state, message.text or "")


@router.callback_query(TournamentEdit.division, Choice.filter(F.flow == DIVISION_FLOW))
async def on_tournament_division_clear(
    callback: CallbackQuery, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        await message.edit_reply_markup(reply_markup=None)
        await _save_division(message, ctx, services, state, None)


async def _save_division(
    message: Message,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
    division: str | None,
) -> None:
    tournament_id = int((await state.get_data())["tournament_id"])
    await services.events.set_division(ctx, tournament_id, division)
    await state.clear()
    await _show_tournament(message, ctx, services, tournament_id)


# --- разбор игры по предложению после матча ---
@router.callback_query(F.data.startswith(f"{callbacks.REVIEW}:"))
async def on_review_offer(
    callback: CallbackQuery, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    parsed = callbacks.parse(callback.data or "")
    message = callback_message(callback)
    match_id = parsed.args[0]
    if parsed.action == callbacks.REVIEW_NO:  # «Не нужно» / «Отмена» — вопрос убирается
        await callback.answer()
        if message:
            await dismiss(message)
        return
    if parsed.action == callbacks.REVIEW_YES:  # шаг 1: время по умолчанию — подтвердить
        proposal = await services.events.review_proposal(ctx, match_id)
        await callback.answer()
        if message is None:
            return
        await message.edit_reply_markup(reply_markup=None)
        if proposal.default_at is None:
            await _ask_review_time(message, state, match_id, proposal.tz)
            return
        when = format_local(proposal.default_at, proposal.tz)
        stamp = int(proposal.default_at.timestamp())
        builder = InlineKeyboardBuilder()
        builder.button(
            text=ru.REVIEW_BTN_PUBLISH.format(when=when),
            callback_data=callbacks.pack(callbacks.REVIEW, callbacks.REVIEW_OK, match_id, stamp),
        )
        builder.button(
            text=ru.REVIEW_BTN_OTHER_TIME,
            callback_data=callbacks.pack(callbacks.REVIEW, callbacks.REVIEW_TIME, match_id),
        )
        builder.button(
            text=ru.BTN_CANCEL,
            callback_data=callbacks.pack(callbacks.REVIEW, callbacks.REVIEW_NO, match_id),
        )
        builder.adjust(1)
        await message.answer(
            ru.REVIEW_PROPOSAL.format(title=html.escape(proposal.match.title), when=when),
            reply_markup=builder.as_markup(),
        )
    elif parsed.action == callbacks.REVIEW_OK:  # шаг 2: подтверждено
        starts_at = datetime.fromtimestamp(parsed.args[1], UTC)
        review = await services.events.create_review(ctx, match_id, starts_at)
        await callback.answer()
        if message:
            squad = await services.squads.get(review.squad_id)
            await message.edit_text(
                ru.REVIEW_CREATED.format(when=format_when(review, squad.timezone)),
                reply_markup=None,
            )
    elif parsed.action == callbacks.REVIEW_TIME:
        proposal = await services.events.review_proposal(ctx, match_id)
        await callback.answer()
        if message:
            await message.edit_reply_markup(reply_markup=None)
            await _ask_review_time(message, state, match_id, proposal.tz)


async def _ask_review_time(message: Message, state: FSMContext, match_id: int, tz: str) -> None:
    await state.set_state(ReviewTime.when)
    await state.update_data(match_id=match_id, tz=tz)
    await message.answer(ru.REVIEW_ASK_TIME)


@router.message(ReviewTime.when, F.text)
async def on_review_time(
    message: Message, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    data = await state.get_data()
    tz = str(data["tz"])
    try:
        naive = parse_local_datetime(message.text or "", today=to_local(utcnow(), tz).date())
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    review = await services.events.create_review(ctx, int(data["match_id"]), localize(naive, tz))
    await message.answer(ru.REVIEW_CREATED.format(when=format_when(review, tz)))


def publication_note(event: EventDTO) -> str:
    """Что на самом деле ушло в группу — а не «опубликовано» в любом случае."""
    if not event.posted:
        return ru.EVENT_NOT_PUBLISHED
    if event.poll_posted is False:
        return ru.EVENT_PUBLISHED_POLL_FAILED
    return ru.EVENT_PUBLISHED_WITH_POLL if event.poll_posted else ru.EVENT_PUBLISHED
