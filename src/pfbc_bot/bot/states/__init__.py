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
    division = State()
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


class TournamentEdit(StatesGroup):
    """Изменение названия и дивизиона турнира."""

    title = State()
    division = State()


class ReviewTime(StatesGroup):
    """Разбор по предложению после матча — другое время."""

    when = State()


class GamePaymentCreation(StatesGroup):
    roster = State()  # заявка: кто играет и платит
    amount = State()
    deadline = State()
    collector = State()
    details = State()
    save_details = State()  # новые реквизиты — сохранить в профиле?
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
