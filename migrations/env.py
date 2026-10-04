"""Alembic: асинхронные миграции. URL — из DATABASE_URL (как у приложения)."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import pfbc_bot.db.models  # noqa: F401 - регистрирует таблицы в metadata
from pfbc_bot.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
DEFAULT_URL = "postgresql+asyncpg://pfbc:pfbc@localhost:5432/pfbc"


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url") or DEFAULT_URL


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(database_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
