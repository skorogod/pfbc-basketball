"""Состояния FSM диалогов."""

from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    birth_date = State()
    mtgame_url = State()
    consent = State()


class ProfileEdit(StatesGroup):
    value = State()


class ScheduleEdit(StatesGroup):
    line = State()


class TrainingEdit(StatesGroup):
    add = State()
    move = State()


class EventCreation(StatesGroup):
    type = State()
    title = State()
    when = State()
    duration = State()
    conflict = State()
    place = State()
    description = State()
    publish = State()


class SettingsEdit(StatesGroup):
    squad = State()
