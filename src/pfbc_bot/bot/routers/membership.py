"""chat_member: синхронизация членства с группами составов (ФТ-0).

Эти апдейты не проходят через AccessMiddleware — они и есть источник правды.
"""

from aiogram import Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import JOIN_TRANSITION, LEAVE_TRANSITION, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated

from pfbc_bot.bot.middlewares.access import to_telegram_user
from pfbc_bot.logging import get_logger
from pfbc_bot.services.container import Services

router = Router(name="membership")
log = get_logger(__name__)


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_member_join(event: ChatMemberUpdated, services: Services) -> None:
    # В группе бот молчит: новичок переходит в бота по ссылке из /invite.
    await services.membership.on_join(event.chat.id, to_telegram_user(event.new_chat_member.user))


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def on_member_leave(event: ChatMemberUpdated, services: Services) -> None:
    await services.membership.on_leave(
        event.chat.id,
        event.new_chat_member.user.id,
        removed=event.new_chat_member.status == ChatMemberStatus.KICKED,
    )


@router.my_chat_member()
async def on_bot_status_changed(event: ChatMemberUpdated) -> None:
    log.info(
        "bot_chat_status",
        chat_id=event.chat.id,
        title=event.chat.title,
        status=event.new_chat_member.status,
    )
