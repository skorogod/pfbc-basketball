"""Сквозной тест слоя aiogram: апдейты → Dispatcher → middleware/роутеры → сервисы → БД.

HTTP к Telegram подменён записывающей сессией: ответы бота проверяются по исходящим запросам.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import DeleteMessage, SendMessage, TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    Update,
    User,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pfbc_bot.bot.factory import create_dispatcher
from pfbc_bot.bot.keyboards.common import Choice, ItemAction
from pfbc_bot.bot.middlewares.access import to_telegram_user
from pfbc_bot.bot.routers.admin import AdminAction, RoleAction
from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import SquadRole, TrainingStatus
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.trainings import TrainingCreate
from pfbc_bot.services.container import Services
from pfbc_bot.texts import ru

from .conftest import ADMIN_TG_ID, FakeNotifier
from .helpers import ctx_of

GROUP_ID = -100777
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
MSK = "Europe/Moscow"
COACH, PLAYER = 100, 2
_dispatcher: Dispatcher | None = None


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        self.requests.append(method)
        await asyncio.sleep(0.005)  # как у настоящей сети: параллельные апдейты перемешиваются
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.requests),
                date=NOW,
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        return True

    async def close(self) -> None:
        return None

    def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError

    def texts_to(self, chat_id: int) -> list[str]:
        return [
            m.text for m in self.requests if isinstance(m, SendMessage) and m.chat_id == chat_id
        ]


class Harness:
    def __init__(self, dp: Dispatcher, bot: Bot, session: RecordingSession) -> None:
        self.dp, self.bot, self.session = dp, bot, session
        self._update_id = 0

    async def feed(self, **kwargs: Any) -> None:
        self._update_id += 1
        await self.dp.feed_update(self.bot, Update(update_id=self._update_id, **kwargs))

    async def private(self, user_id: int, text: str) -> None:
        await self.feed(message=_message(Chat(id=user_id, type="private"), user_id, text))

    async def group(self, user_id: int, text: str) -> None:
        await self.feed(
            message=_message(Chat(id=GROUP_ID, type="supergroup", title="PFBC"), user_id, text)
        )

    async def press(self, user_id: int, data: str) -> None:
        await self.feed(
            callback_query=CallbackQuery(
                id=str(self._update_id),
                from_user=_user(user_id),
                chat_instance="ci",
                data=data,
                message=_message(Chat(id=user_id, type="private"), user_id, "…"),
            )
        )

    async def member(self, user_id: int, *, joined: bool) -> None:
        user = _user(user_id)
        left, member = ChatMemberLeft(user=user), ChatMemberMember(user=user)
        await self.feed(
            chat_member=ChatMemberUpdated(
                chat=Chat(id=GROUP_ID, type="supergroup"),
                from_user=user,
                date=NOW,
                old_chat_member=left if joined else member,
                new_chat_member=member if joined else left,
            )
        )


def _user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name=f"user{user_id}")


def _message(chat: Chat, user_id: int, text: str) -> Message:
    return Message(message_id=1, date=NOW, chat=chat, from_user=_user(user_id), text=text)


@pytest.fixture
def harness(services: Services, engine: AsyncEngine, uow_factory: UowFactory) -> Harness:
    global _dispatcher
    session = RecordingSession()
    bot = Bot("42:TEST", session=session)
    # роутеры aiogram — синглтоны модулей, поэтому диспетчер один на сессию; сервисы — свои
    if _dispatcher is None:
        _dispatcher = create_dispatcher(
            services, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        )
    _dispatcher["services"] = services
    return Harness(_dispatcher, bot, session)


async def test_access_bind_join_register_leave(
    harness: Harness, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    # посторонний получает отказ и только один раз
    await harness.private(1, "/start")
    await harness.private(1, "/help")
    assert harness.session.texts_to(1) == [ru.ACCESS_DENIED]

    # /bind: команда удаляется из группы, подтверждение — админу в личку, в группе тишина
    await bind(harness)
    assert any(isinstance(m, DeleteMessage) for m in harness.session.requests)
    assert harness.session.texts_to(GROUP_ID) == []
    assert harness.session.texts_to(ADMIN_TG_ID) == [
        ru.BIND_DONE.format(chat="PFBC", squad="Взрослые")
    ]

    # вступление в группу — бот ничего не пишет в группу
    await harness.member(1, joined=True)
    assert notifier.to(GROUP_ID) == [] and harness.session.texts_to(GROUP_ID) == []

    # регистрация по ссылке из /invite: ФИО → дата рождения → пропуск mtgame → согласие
    await harness.private(1, "/start reg_1")
    await harness.private(1, "Иванов Иван")
    await harness.private(1, "32.13.2000")  # некорректная дата — переспрашиваем
    await harness.private(1, "21.03.2005")
    await harness.press(1, Choice(flow="reg", value="skip").pack())
    await harness.press(1, Choice(flow="reg", value="consent").pack())
    texts = harness.session.texts_to(1)
    assert ru.CLUB_INFO in texts and ru.REG_ASK_BIRTH in texts
    assert texts[-1] == ru.REG_DONE.format(name="Иванов Иван")
    async with uow_factory() as uow:
        user = await uow.users.get_by_tg_id(1)
        assert user is not None and user.full_name == "Иванов Иван" and user.pd_consent_at

    # удалён из группы → команды больше не принимаются; отказ уже сообщался — бот молчит
    await harness.member(1, joined=False)
    before = len(harness.session.requests)
    await harness.private(1, "/profile")
    assert len(harness.session.requests) == before


async def test_roles_invite_and_settings_in_private_chat(
    harness: Harness, services: Services
) -> None:
    await bind(harness)
    await harness.member(COACH, joined=True)
    await harness.member(PLAYER, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    coach_id = (await ctx_of(services, COACH)).user.id

    # /roles: админ назначает тренера кнопками
    await harness.private(ADMIN_TG_ID, "/roles")
    await harness.press(
        ADMIN_TG_ID, RoleAction(squad_id=squad.id, user_id=coach_id, action="open").pack()
    )
    await harness.press(
        ADMIN_TG_ID, RoleAction(squad_id=squad.id, user_id=coach_id, action="coach-1").pack()
    )
    assert (await ctx_of(services, COACH)).has_role(squad.id, SquadRole.COACH)

    # /admins: обычный участник не видит список; админ назначает тренера вторым админом
    await harness.private(PLAYER, "/admins")
    assert harness.session.texts_to(PLAYER)[-1] == ru.NO_PERMISSION
    await harness.private(ADMIN_TG_ID, "/admins")
    await harness.press(ADMIN_TG_ID, AdminAction(action="add").pack())
    await harness.press(ADMIN_TG_ID, AdminAction(action="grant", user_id=coach_id).pack())
    assert (await ctx_of(services, COACH)).is_admin
    assert ru.ADMIN_GRANTED.format(name="user100") in harness.session.texts_to(ADMIN_TG_ID)
    await harness.press(ADMIN_TG_ID, AdminAction(action="revoke", user_id=coach_id).pack())
    assert not (await ctx_of(services, COACH)).is_admin

    # /invite: тренеру — ссылка, игроку — отказ
    await harness.private(COACH, "/invite")
    assert f"https://t.me/pfbc_test_bot?start=reg_{squad.id}" in harness.session.texts_to(COACH)[-1]
    await harness.private(PLAYER, "/invite")
    assert harness.session.texts_to(PLAYER)[-1] == ru.NO_SQUADS_FOR_ACTION

    # /set: ключ состава → применяется к составу; глобальный — сразу; неизвестный — отказ
    await harness.private(ADMIN_TG_ID, "/set duty_count 3")
    assert (await services.settings.get_squad(squad.id)).duty_count == 3
    await harness.private(ADMIN_TG_ID, "/set inactive_retention_days 180")
    assert (await services.settings.get_global()).inactive_retention_days == 180
    await harness.private(ADMIN_TG_ID, "/set no_such_key 1")
    assert harness.session.texts_to(ADMIN_TG_ID)[-1] == ru.SETTINGS_UNKNOWN.format(
        key="no_such_key"
    )
    # обычный участник настройки не меняет
    await harness.private(COACH, "/set duty_count 1")
    assert harness.session.texts_to(COACH)[-1] == ru.NO_PERMISSION


async def test_event_add_warns_about_overlap_and_publishes(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    await bind(harness)
    await harness.member(COACH, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach_id = (await ctx_of(services, COACH)).user.id
    await services.squads.set_role(admin, squad.id, coach_id, SquadRole.COACH, grant=True)

    day = (datetime.now(UTC) + timedelta(days=30)).date()
    await services.trainings.add_one_off(
        await ctx_of(services, COACH),
        TrainingCreate(
            squad_id=squad.id, starts_at=local_to_utc(day, time(19, 0), MSK), venue="Зал №5"
        ),
    )

    await harness.private(COACH, "/event_add")
    await harness.press(COACH, Choice(flow="ev_type", value="meeting").pack())
    await harness.private(COACH, "Собрание команды")
    await harness.private(COACH, f"{day:%d.%m.%Y} 20:00")
    await harness.press(COACH, Choice(flow="ev_dur", value="60").pack())
    warning = harness.session.texts_to(COACH)[-1]
    assert ru.EVENT_CONFLICT_HEADER in warning and "Тренировка, Зал №5" in warning

    await harness.press(COACH, Choice(flow="ev_conf", value="go").pack())
    await harness.press(COACH, Choice(flow="ev_place", value="skip").pack())
    await harness.press(COACH, Choice(flow="ev_descr", value="skip").pack())
    await harness.press(COACH, Choice(flow="ev_pub", value="yes").pack())

    assert ru.EVENT_PUBLISHED in harness.session.texts_to(COACH)[-1]
    post = notifier.to(GROUP_ID)[-1]
    assert "Собрание команды" in post.text and "20:00–21:00" in post.text
    events = await services.events.upcoming([squad.id])
    assert [(e.title, e.duration_minutes) for e, _, _ in events] == [("Собрание команды", 60)]


async def test_accidental_duty_button_press_changes_nothing(
    harness: Harness, services: Services, notifier: FakeNotifier, uow_factory: UowFactory
) -> None:
    await bind(harness)
    for tg_id in (COACH, PLAYER, 3):
        await harness.member(tg_id, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach = await ctx_of(services, COACH)
    await services.squads.set_role(admin, squad.id, coach.user.id, SquadRole.CAPTAIN, grant=True)
    await services.squads.set_role(admin, squad.id, coach.user.id, SquadRole.COACH, grant=True)
    async with uow_factory() as uow:  # участники давно в составе — не новички
        for member, _ in await uow.members.list_active_for_squad(squad.id):
            member.first_joined_at = NOW - timedelta(days=365)
        await uow.commit()

    # опрос — с полуночи, чтобы тест не зависел от времени суток
    await services.settings.set_squad_value(
        squad.id, "training_poll_time", "00:00", actor_user_id=None
    )
    starts = datetime.now(UTC) + timedelta(hours=2)
    training = await services.trainings.add_one_off(
        await ctx_of(services, COACH),
        TrainingCreate(squad_id=squad.id, starts_at=starts, venue="Зал №5"),
    )
    assert await services.polls.publish_training_polls(now=starts - timedelta(minutes=1)) == 1
    for tg_id in (PLAYER, 3):
        await services.polls.record_answer("poll-1", tg_id, [0])
    # за минуту до начала дежурные назначены при любом правиле (ранняя тренировка или нет)
    await services.duty.assign_due(now=starts - timedelta(minutes=1))
    async with uow_factory() as uow:
        duties = {a.id: a.status for a in await uow.trainings.list_duties(training.id)}
    assert len(duties) == 2

    # капитан нажимает «Заменить» в сводке → вопрос → «Отмена» — ничего не изменилось
    summary = notifier.to(COACH)[-1].buttons()
    replace_btn = next(data for text, data in summary.items() if "Заменить" in text)
    await harness.press(COACH, replace_btn)
    assert "Заменить дежурного" in harness.session.texts_to(COACH)[-1]
    await harness.press(COACH, "duty:no")
    async with uow_factory() as uow:
        after = {a.id: a.status for a in await uow.trainings.list_duties(training.id)}
    assert after == duties

    # дежурный: «Не могу дежурить» → вопрос → подтверждение — снят с дежурства
    decline_btn = notifier.to(PLAYER)[-1].buttons()[ru.DUTY_BTN_DECLINE]
    await harness.press(PLAYER, decline_btn)
    assert harness.session.texts_to(PLAYER)[-1].startswith("Отказаться от дежурства")
    await harness.press(PLAYER, decline_btn.replace(":decl:", ":declok:"))
    player_id = (await ctx_of(services, PLAYER)).user.id
    async with uow_factory() as uow:
        statuses = {a.user_id: a.status for a in await uow.trainings.list_duties(training.id)}
    assert statuses[player_id] == "replaced"


async def bind(harness: Harness) -> None:
    await harness.group(ADMIN_TG_ID, "/bind Взрослые")


async def test_double_press_publishes_event_once(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    await bind(harness)
    await harness.member(COACH, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach_id = (await ctx_of(services, COACH)).user.id
    await services.squads.set_role(admin, squad.id, coach_id, SquadRole.COACH, grant=True)

    day = (datetime.now(UTC) + timedelta(days=30)).date()
    await harness.private(COACH, "/event_add")
    await harness.press(COACH, Choice(flow="ev_type", value="meeting").pack())
    await harness.private(COACH, "Собрание команды")
    await harness.private(COACH, f"{day:%d.%m.%Y} 20:00")
    await harness.press(COACH, Choice(flow="ev_dur", value="60").pack())
    await harness.press(COACH, Choice(flow="ev_place", value="skip").pack())
    await harness.press(COACH, Choice(flow="ev_descr", value="skip").pack())
    publish = Choice(flow="ev_pub", value="yes").pack()
    await asyncio.gather(harness.press(COACH, publish), harness.press(COACH, publish))

    assert len(await services.events.upcoming([squad.id])) == 1
    assert len([m for m in notifier.to(GROUP_ID) if "Собрание команды" in m.text]) == 1
    # второе нажатие не доходит до создания: нет и ложного «пересекается с самим собой»
    assert not any(ru.EVENT_CONFLICT_HEADER in t for t in harness.session.texts_to(COACH))


async def test_training_cancel_needs_confirmation(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    await bind(harness)
    await harness.member(COACH, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach = await ctx_of(services, COACH)
    await services.squads.set_role(admin, squad.id, coach.user.id, SquadRole.COACH, grant=True)
    training = await services.trainings.add_one_off(
        await ctx_of(services, COACH),
        TrainingCreate(
            squad_id=squad.id,
            starts_at=datetime.now(UTC) + timedelta(days=3),
            venue="Зал №5",
        ),
    )

    await harness.press(COACH, ItemAction(kind="tr", action="cancel", item_id=training.id).pack())
    assert "Отменить тренировку" in harness.session.texts_to(COACH)[-1]
    assert (await services.trainings.get(training.id)).status == TrainingStatus.PLANNED

    await harness.press(COACH, ItemAction(kind="confirm", action="no", item_id=0).pack())
    assert (await services.trainings.get(training.id)).status == TrainingStatus.PLANNED

    await harness.press(
        COACH, ItemAction(kind="tr", action="cancel_ok", item_id=training.id).pack()
    )
    assert (await services.trainings.get(training.id)).status == TrainingStatus.CANCELLED
    assert any("отменена" in m.text for m in notifier.to(GROUP_ID))


async def test_group_message_discovers_member_who_joined_before_bot(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    await bind(harness)
    # бот о пользователе 5 не знал: вступил до подключения бота. Написал в группе — участник
    await harness.group(5, "Всем привет!")
    ctx = await ctx_of(services, 5)
    assert ctx.is_member
    # в группу бот при этом ничего не пишет
    assert notifier.to(GROUP_ID) == [] and harness.session.texts_to(GROUP_ID) == []

    # служебное «вышел из группы» не заводит членство
    await harness.feed(
        message=Message(
            message_id=2,
            date=NOW,
            chat=Chat(id=GROUP_ID, type="supergroup", title="PFBC"),
            from_user=_user(6),
            left_chat_member=_user(6),
        )
    )
    assert await services.access.load_context(to_telegram_user(_user(6))) is None
