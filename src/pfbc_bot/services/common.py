"""Общие помощники сервисов: адресация сообщений состава, рассылка руководству."""

from collections.abc import Awaitable, Callable, Iterable

from pfbc_bot.db.models import Squad, User
from pfbc_bot.domain.enums import STAFF_ROLES, SquadRole, TopicPurpose
from pfbc_bot.repositories.uow import UnitOfWork
from pfbc_bot.schemas.squads import Destination
from pfbc_bot.schemas.users import UserDTO
from pfbc_bot.services.ports import Keyboard, Notifier


async def resolve_destination(
    uow: UnitOfWork, squad_id: int, purpose: TopicPurpose
) -> Destination | None:
    """Группа состава + топик назначения. Нет топика — общий топик, иначе общий раздел."""
    chat = await uow.squads.get_chat_for_squad(squad_id)
    if chat is None:
        return None
    for candidate in (purpose, TopicPurpose.GENERAL):
        topic = await uow.squads.get_topic(squad_id, candidate)
        if topic is not None:
            return Destination(
                squad_id=squad_id, chat_id=chat.chat_id, thread_id=topic.message_thread_id
            )
    return Destination(squad_id=squad_id, chat_id=chat.chat_id)


async def send_to_squad(
    uow: UnitOfWork,
    notifier: Notifier,
    squad_id: int,
    purpose: TopicPurpose,
    text: str,
    *,
    keyboard: Keyboard | None = None,
) -> int | None:
    destination = await resolve_destination(uow, squad_id, purpose)
    if destination is None:
        return None
    return await notifier.send_message(
        destination.chat_id, text, thread_id=destination.thread_id, keyboard=keyboard
    )


AfterCommit = Callable[[], Awaitable[object]]


async def deferred_to_squad(
    uow: UnitOfWork, notifier: Notifier, squad_id: int, purpose: TopicPurpose, text: str
) -> AfterCommit:
    """Адрес — сейчас (в транзакции), отправка — после commit: сетевой вызов не держит
    транзакцию и блокировки открытыми."""
    destination = await resolve_destination(uow, squad_id, purpose)

    async def send() -> object:
        if destination is None:
            return None
        return await notifier.send_message(
            destination.chat_id, text, thread_id=destination.thread_id
        )

    return send


async def run_after_commit(actions: Iterable[AfterCommit]) -> None:
    for action in actions:
        await action()


async def send_direct(
    notifier: Notifier, user: User, text: str, *, keyboard: Keyboard | None = None
) -> bool:
    """Личное сообщение. Обновляет признак dm_available по факту доставки."""
    sent = await notifier.send_message(user.tg_user_id, text, keyboard=keyboard) is not None
    user.dm_available = sent
    return sent


async def notify_staff(
    uow: UnitOfWork,
    notifier: Notifier,
    squad_id: int,
    text: str,
    *,
    roles: Iterable[SquadRole] = STAFF_ROLES,
    keyboard: Keyboard | None = None,
) -> int:
    """Личные сообщения тренерам/капитанам состава. Возвращает число доставленных."""
    delivered = 0
    for user in await uow.members.users_with_roles(squad_id, roles):
        delivered += await send_direct(notifier, user, text, keyboard=keyboard)
    return delivered


async def notify_admins(
    uow: UnitOfWork, notifier: Notifier, admin_tg_ids: Iterable[int], text: str
) -> None:
    recipients = {u.tg_user_id for u in await uow.users.list_admins()} | set(admin_tg_ids)
    for tg_id in recipients:
        await notifier.send_message(tg_id, text)


def squad_tz(squad: Squad | None, default: str = "Europe/Moscow") -> str:
    return squad.timezone if squad is not None else default


def to_dto(user: User) -> UserDTO:
    return UserDTO.model_validate(user)


def profile(user: User | None) -> str:
    """Имя-ссылка на профиль для сообщений в личку («?» — пользователя уже нет)."""
    return to_dto(user).profile_html if user is not None else "?"


def mention(user: User | None) -> str:
    """Упоминание для сообщений в группе: человек получает уведомление."""
    return to_dto(user).mention_html if user is not None else "?"
