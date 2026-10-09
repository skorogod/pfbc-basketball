"""Процесс bot: приём апдейтов Telegram (webhook в prod, polling в dev)."""

import asyncio

from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from pfbc_bot.bot.factory import ALLOWED_UPDATES, create_dispatcher, setup_bot_commands
from pfbc_bot.config import BotMode, Settings, get_settings
from pfbc_bot.logging import get_logger, setup_logging
from pfbc_bot.runtime import Runtime, build_runtime

log = get_logger(__name__)


async def healthz(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def run_polling(runtime: Runtime, settings: Settings) -> None:
    """Polling — для разработки. /healthz поднимается и здесь: healthcheck контейнера не
    падает, если в .env оставлен BOT_MODE=polling (а в логе — предупреждение)."""
    dp = create_dispatcher(runtime.services, runtime.session_factory)
    await runtime.bot.delete_webhook(drop_pending_updates=False)
    app = web.Application()
    app.router.add_get("/healthz", healthz)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, settings.web_host, settings.web_port).start()
    log.warning("polling_started", hint="в prod нужен BOT_MODE=webhook: Caddy проксирует вебхук")
    try:
        await dp.start_polling(runtime.bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        await runner.cleanup()


async def run_webhook(runtime: Runtime, settings: Settings) -> None:
    dp = create_dispatcher(runtime.services, runtime.session_factory)
    await runtime.bot.set_webhook(
        settings.webhook_url,
        secret_token=settings.webhook_secret_token.get_secret_value(),
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=False,
    )
    app = web.Application()
    app.router.add_get("/healthz", healthz)
    SimpleRequestHandler(
        dispatcher=dp,
        bot=runtime.bot,
        secret_token=settings.webhook_secret_token.get_secret_value(),
    ).register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=runtime.bot)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, settings.web_host, settings.web_port).start()
    log.info("webhook_started", host=settings.web_host, port=settings.web_port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    runtime = await build_runtime(settings)
    try:
        await setup_bot_commands(runtime.bot)
        if settings.bot_mode == BotMode.WEBHOOK:
            await run_webhook(runtime, settings)
        else:
            await run_polling(runtime, settings)
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
