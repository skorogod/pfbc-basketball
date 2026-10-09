"""Сборка Dispatcher: хранилище FSM, middleware, роутеры, меню команд."""

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import BotCommandScopeAllPrivateChats
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pfbc_bot.bot.commands import BASE_COMMANDS, CANCEL
from pfbc_bot.bot.fsm_storage import PostgresStorage
from pfbc_bot.bot.middlewares.access import AccessMiddleware
from pfbc_bot.bot.middlewares.commands import CommandMenuMiddleware, CommandResetMiddleware
from pfbc_bot.bot.routers import (
    admin,
    duty,
    errors,
    events,
    game_payments,
    membership,
    permissions,
    polls,
    profile,
    registration,
    trainings,
)
from pfbc_bot.services.container import Services

ALLOWED_UPDATES = ["message", "callback_query", "poll_answer", "chat_member", "my_chat_member"]


def create_dispatcher(
    services: Services, session_factory: async_sessionmaker[AsyncSession]
) -> Dispatcher:
    # апдейты одного пользователя — по очереди: двойное нажатие не проходит один шаг диалога
    # дважды (например, не создаёт два события с двумя постами в группе)
    dp = Dispatcher(
        storage=PostgresStorage(session_factory), events_isolation=SimpleEventIsolation()
    )
    dp["services"] = services

    access, menu = AccessMiddleware(), CommandMenuMiddleware()
    # poll_answer — без AccessMiddleware: участника состава опроса проверяет PollService
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(access)
        observer.outer_middleware(menu)
    dp.message.outer_middleware(CommandResetMiddleware())

    dp.include_routers(
        errors.router,
        membership.router,
        polls.router,
        duty.router,
        admin.router,
        permissions.router,
        registration.router,
        profile.router,
        trainings.router,
        events.router,
        game_payments.router,
    )
    return dp


async def setup_bot_commands(bot: Bot) -> None:
    """Общее меню — базовые команды; остальное каждый видит в своём меню (по правам)."""
    await bot.set_my_commands([*BASE_COMMANDS, CANCEL], scope=BotCommandScopeAllPrivateChats())
