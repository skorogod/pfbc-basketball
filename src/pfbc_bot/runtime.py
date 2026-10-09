"""Общая инициализация процессов bot и worker."""

from dataclasses import dataclass

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pfbc_bot.config import Settings
from pfbc_bot.db.session import create_engine, create_session_factory
from pfbc_bot.integrations.telegram.notifier import TelegramNotifier
from pfbc_bot.repositories.uow import make_uow_factory
from pfbc_bot.services.container import Services, build_services


@dataclass(slots=True)
class Runtime:
    bot: Bot
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    services: Services

    async def close(self) -> None:
        await self.bot.session.close()
        await self.engine.dispose()


async def build_runtime(settings: Settings) -> Runtime:
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError as exc:
        await bot.session.close()
        raise SystemExit("BOT_TOKEN недействителен: Telegram ответил Unauthorized") from exc
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    notifier = TelegramNotifier(bot, me.username or "")
    services = build_services(make_uow_factory(session_factory), notifier, settings.admin_tg_ids)
    return Runtime(bot=bot, engine=engine, session_factory=session_factory, services=services)
