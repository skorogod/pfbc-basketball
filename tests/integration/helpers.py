"""Сценарные помощники: состав с группой, участники с ролями, контекст пользователя."""

from datetime import datetime

from pfbc_bot.domain.enums import SquadRole, TopicPurpose
from pfbc_bot.schemas.squads import SquadDTO, UserContext
from pfbc_bot.schemas.users import TelegramUser
from pfbc_bot.services.container import Services

from .conftest import ADMIN_TG_ID

CHAT_ID = -100500
TRAININGS_THREAD = 7
GREETINGS_THREAD = 9
MATCH_THREAD = 11


def tg(tg_id: int, name: str | None = None) -> TelegramUser:
    return TelegramUser(tg_user_id=tg_id, first_name=name or f"user{tg_id}")


async def ctx_of(services: Services, tg_id: int) -> UserContext:
    ctx = await services.access.load_context(tg(tg_id))
    assert ctx is not None
    return ctx


async def setup_squad(services: Services, name: str = "Взрослые") -> SquadDTO:
    admin = await ctx_of(services, ADMIN_TG_ID)
    squad = await services.squads.bind_chat(admin, CHAT_ID, "PFBC Взрослые", name)
    await services.squads.bind_topic(admin, CHAT_ID, TopicPurpose.TRAININGS, TRAININGS_THREAD)
    await services.squads.bind_topic(admin, CHAT_ID, TopicPurpose.GREETINGS, GREETINGS_THREAD)
    await services.squads.bind_topic(admin, CHAT_ID, TopicPurpose.MATCH, MATCH_THREAD)
    return squad


async def join(
    services: Services, tg_id: int, *, at: datetime, role: SquadRole | None = None
) -> None:
    result = await services.membership.on_join(CHAT_ID, tg(tg_id), now=at)
    assert result is not None
    if role is not None:
        admin = await ctx_of(services, ADMIN_TG_ID)
        await services.squads.set_role(admin, result.squad.id, result.user.id, role, grant=True)
