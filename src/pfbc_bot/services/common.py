"""Общие помощники сервисов: адресация сообщений состава, рассылка руководству."""

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from pfbc_bot.db.models import Squad, User
from pfbc_bot.domain.enums import STAFF_ROLES, SquadRole, TopicPurpose
from pfbc_bot.repositories.uow import UnitOfWork
from pfbc_bot.schemas.squads import Destination
from pfbc_bot.schemas.users import UserDTO
from pfbc_bot.services.ports import Keyboard, Notifier
from pfbc_bot.texts import ru


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
    return await send_direct_id(notifier, user, text, keyboard=keyboard) is not None


async def send_direct_id(
    notifier: Notifier, user: User, text: str, *, keyboard: Keyboard | None = None
) -> int | None:
    """Как send_direct, но возвращает message_id (None — не доставлено): чтобы потом снять
    кнопку с сообщения."""
    message_id = await notifier.send_message(user.tg_user_id, text, keyboard=keyboard)
    user.dm_available = message_id is not None
    return message_id


async def send_closable_notice(
    notifier: Notifier, users: Iterable[User], text: str, keyboard: Keyboard
) -> dict[str, Any] | None:
    """«Все оплатили / все прислали» с кнопкой «Завершить сбор» — в личку. Возвращает, что
    запомнить у сбора для close_notice ({"text", "messages": [[chat_id, message_id]]});
    None — никому не дошло."""
    messages = []
    for user in users:
        message_id = await send_direct_id(notifier, user, text, keyboard=keyboard)
        if message_id is not None:
            messages.append([user.tg_user_id, message_id])
    return {"text": text, "messages": messages} if messages else None


async def close_notice(notifier: Notifier, notice: dict[str, Any] | None) -> None:
    """Сбор завершён: в разосланных send_closable_notice сообщениях снять кнопку и отметить."""
    if not notice:
        return
    text = notice["text"] + ru.COLLECTION_CLOSED_MARK
    for chat_id, message_id in notice["messages"]:
        await notifier.edit_message(chat_id, message_id, text)


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
