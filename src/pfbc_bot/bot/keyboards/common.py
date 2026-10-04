"""Клавиатуры и callback-данные интерфейса бота (только слой aiogram)."""

from collections.abc import Iterable

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pfbc_bot.schemas.squads import SquadDTO
from pfbc_bot.texts import ru


class SquadPick(CallbackData, prefix="sq"):
    flow: str
    squad_id: int


class Choice(CallbackData, prefix="ch"):
    flow: str
    value: str


class ItemAction(CallbackData, prefix="it"):
    """Действие над объектом: schedule/training/event + id."""

    kind: str
    action: str
    item_id: int


def squads_keyboard(squads: Iterable[SquadDTO], flow: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for squad in squads:
        builder.button(text=squad.name, callback_data=SquadPick(flow=flow, squad_id=squad.id))
    builder.adjust(1)
    return builder.as_markup()


def choices_keyboard(
    flow: str, options: Iterable[tuple[str, str]], width: int = 2
) -> InlineKeyboardMarkup:
    """options: (value, text)."""
    builder = InlineKeyboardBuilder()
    for value, text in options:
        builder.button(text=text, callback_data=Choice(flow=flow, value=value))
    builder.adjust(width)
    return builder.as_markup()


def skip_keyboard(flow: str) -> InlineKeyboardMarkup:
    return choices_keyboard(flow, [("skip", ru.BTN_SKIP)])


def yes_no_keyboard(flow: str) -> InlineKeyboardMarkup:
    return choices_keyboard(flow, [("yes", ru.BTN_YES), ("no", ru.BTN_NO)])
