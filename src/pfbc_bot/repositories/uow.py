"""Unit of Work: одна транзакция и набор репозиториев поверх общей сессии."""

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pfbc_bot.repositories.events import EventsRepository
from pfbc_bot.repositories.matches import MatchesRepository
from pfbc_bot.repositories.members import MembersRepository
from pfbc_bot.repositories.polls import PollsRepository
from pfbc_bot.repositories.squads import SquadsRepository
from pfbc_bot.repositories.system import SystemRepository
from pfbc_bot.repositories.trainings import TrainingsRepository
from pfbc_bot.repositories.users import UsersRepository


class UnitOfWork:
    users: UsersRepository
    squads: SquadsRepository
    members: MembersRepository
    trainings: TrainingsRepository
    polls: PollsRepository
    events: EventsRepository
    matches: MatchesRepository
    system: SystemRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> Self:
        self.session = self._session_factory()
        self.users = UsersRepository(self.session)
        self.squads = SquadsRepository(self.session)
        self.members = MembersRepository(self.session)
        self.trainings = TrainingsRepository(self.session)
        self.polls = PollsRepository(self.session)
        self.events = EventsRepository(self.session)
        self.matches = MatchesRepository(self.session)
        self.system = SystemRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()


UowFactory = Callable[[], UnitOfWork]


def make_uow_factory(session_factory: async_sessionmaker[AsyncSession]) -> UowFactory:
    return lambda: UnitOfWork(session_factory)
