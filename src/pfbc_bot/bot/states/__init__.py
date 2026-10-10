"""Состояния FSM диалогов."""

from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    birth_date = State()
    mtgame_url = State()
    phone = State()
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
    tournament = State()  # матч: турнир, в рамках которого он играется
    title = State()
    when = State()
    tournament_start = State()
    tournament_length = State()
    game_price = State()
    duration = State()
    conflict = State()
    place = State()
    description = State()
    publish = State()


class EventMove(StatesGroup):
    when = State()


class TournamentPrice(StatesGroup):
    """Изменение стоимости одной игры турнира."""

    value = State()


class ReviewTime(StatesGroup):
    """Разбор по предложению после матча — другое время."""

    when = State()


class GamePaymentCreation(StatesGroup):
    amount = State()
    deadline = State()
    collector = State()
    details = State()
    confirm = State()


class ReceiptRequestCreation(StatesGroup):
    """Опрос о квитанциях за месяц вручную: срок."""

    deadline = State()


class PaymentDetailsInput(StatesGroup):
    """Реквизиты для сбора денег: способ → телефон или карта → банк."""

    method = State()
    value = State()
    bank = State()


class SettingsEdit(StatesGroup):
    squad = State()
