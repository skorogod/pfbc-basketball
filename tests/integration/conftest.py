"""Интеграционные тесты: настоящий PostgreSQL + фейковый Telegram.

БД — из TEST_DATABASE_URL или временный контейнер testcontainers.

Схема создаётся миграциями Alembic — заодно проверяются и они.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pfbc_bot.db.base import Base
from pfbc_bot.repositories.uow import UowFactory, make_uow_factory
from pfbc_bot.services.container import Services, build_services
from pfbc_bot.services.ports import Keyboard, SentPoll

ROOT = Path(__file__).resolve().parents[2]
ADMIN_TG_ID = 999


@dataclass
class SentMessage:
    chat_id: int
    text: str
    thread_id: int | None
    keyboard: Keyboard | None
    reply_to: int | None = None
    message_id: int = 0

    def buttons(self) -> dict[str, str]:
        """Текст кнопки → callback_data."""
        return {b.text: b.callback_data or "" for row in self.keyboard or [] for b in row}


@dataclass
class FakeNotifier:
    """Запоминает отправленное; членство в чатах и доступность личек задаются тестом."""

    bot_username: str = "pfbc_test_bot"
    messages: list[SentMessage] = field(default_factory=list)
    polls: list[tuple[int, str, int | None]] = field(default_factory=list)
    chat_members: dict[int, set[int]] = field(default_factory=dict)
    blocked_dm: set[int] = field(default_factory=set)
    edits: list[tuple[int, int, str]] = field(default_factory=list)
    stopped_polls: list[tuple[int, int]] = field(default_factory=list)
    # отправка с этим порядковым номером не доставлена (notifier вернул None)
    fail_on: set[int] = field(default_factory=set)
    # отправка с этим порядковым номером падает исключением (непредвиденный сбой посреди задачи)
    crash_on: set[int] = field(default_factory=set)
    _attempts: int = 0
    _poll_seq: int = 0

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        keyboard: Keyboard | None = None,
        reply_to: int | None = None,
    ) -> int | None:
        self._attempts += 1
        if self._attempts in self.crash_on:
            raise RuntimeError("simulated crash")
        if chat_id in self.blocked_dm or self._attempts in self.fail_on:
            return None
        message_id = len(self.messages) + 1
        self.messages.append(SentMessage(chat_id, text, thread_id, keyboard, reply_to, message_id))
        return message_id

    async def edit_message(
        self, chat_id: int, message_id: int, text: str, *, keyboard: Keyboard | None = None
    ) -> bool:
        self.edits.append((chat_id, message_id, text))
        return True

    async def send_poll(
        self, chat_id: int, question: str, options: Sequence[str], *, thread_id: int | None = None
    ) -> SentPoll | None:
        self._poll_seq += 1
        self.polls.append((chat_id, question, thread_id))
        return SentPoll(poll_id=f"poll-{self._poll_seq}", message_id=1000 + self._poll_seq)

    async def stop_poll(self, chat_id: int, message_id: int) -> bool:
        self.stopped_polls.append((chat_id, message_id))
        return True

    async def is_chat_member(self, chat_id: int, tg_user_id: int) -> bool | None:
        return tg_user_id in self.chat_members.get(chat_id, set())

    def to(self, chat_id: int) -> list[SentMessage]:
        return [m for m in self.messages if m.chat_id == chat_id]


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        _migrate(url)
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        _migrate(url)
        yield url


def _migrate(url: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def uow_factory(engine: AsyncEngine) -> UowFactory:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return make_uow_factory(async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession))


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def services(uow_factory: UowFactory, notifier: FakeNotifier) -> Services:
    return build_services(uow_factory, notifier, [ADMIN_TG_ID])


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
