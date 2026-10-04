"""Сборка Dispatcher: хранилище FSM, middleware, роутеры, меню команд."""

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pfbc_bot.bot.fsm_storage import PostgresStorage
from pfbc_bot.bot.middlewares.access import AccessMiddleware
from pfbc_bot.bot.routers import (
    admin,
    duty,
    errors,
    events,
    membership,
    polls,
    profile,
    registration,
    trainings,
)
from pfbc_bot.services.container import Services

ALLOWED_UPDATES = ["message", "callback_query", "poll_answer", "chat_member", "my_chat_member"]

PRIVATE_COMMANDS = [
    BotCommand(command="start", description="Начать / регистрация"),
    BotCommand(command="help", description="Список команд"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="events", description="Ближайшие события"),
    BotCommand(command="schedule", description="Расписание тренировок"),
    BotCommand(command="trainings", description="Ближайшие тренировки"),
    BotCommand(command="attendance", description="Кто придёт на тренировку"),
    BotCommand(command="duty", description="Дежурные: замена и ручной выбор"),
    BotCommand(command="event_add", description="Добавить событие"),
    BotCommand(command="invite", description="Ссылка на регистрацию для новичка"),
    BotCommand(command="cancel", description="Отменить ввод"),
]


def create_dispatcher(
    services: Services, session_factory: async_sessionmaker[AsyncSession]
) -> Dispatcher:
    dp = Dispatcher(storage=PostgresStorage(session_factory))
    dp["services"] = services

    access = AccessMiddleware()
    for observer in (dp.message, dp.callback_query, dp.poll_answer):
        observer.outer_middleware(access)

    dp.include_routers(
        errors.router,
        membership.router,
        polls.router,
        duty.router,
        admin.router,
        registration.router,
        profile.router,
        trainings.router,
        events.router,
    )
    return dp


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
