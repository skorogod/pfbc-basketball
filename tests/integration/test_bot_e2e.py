"""Сквозной тест слоя aiogram: апдейты → Dispatcher → middleware/роутеры → сервисы → БД.

HTTP к Telegram подменён записывающей сессией: ответы бота проверяются по исходящим запросам.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    SendMessage,
    SetMyCommands,
    TelegramMethod,
)
from aiogram.types import (
    BotCommandScopeChat,
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
from pfbc_bot.bot.keyboards.common import Choice, ItemAction, SquadPick
from pfbc_bot.bot.middlewares.access import to_telegram_user
from pfbc_bot.bot.routers.admin import AdminAction, RoleAction
from pfbc_bot.bot.routers.events import TournamentLink
from pfbc_bot.bot.routers.game_payments import DetailsAction
from pfbc_bot.bot.routers.permissions import PermAction
from pfbc_bot.domain.dates import local_to_utc
from pfbc_bot.domain.enums import SquadRole, TrainingStatus
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.schemas.events import EventCreate
from pfbc_bot.schemas.payments import GamePaymentCreate, PaymentDetails, PaymentMethod
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
        self.answered: set[str] = set()

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        self.requests.append(method)
        await asyncio.sleep(0.005)  # как у настоящей сети: параллельные апдейты перемешиваются
        if isinstance(method, AnswerCallbackQuery):  # как Telegram: на нажатие — один ответ
            if method.callback_query_id in self.answered:
                raise TelegramBadRequest(method=method, message="Bad Request: query is too old")
            self.answered.add(method.callback_query_id)
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

    # регистрация по ссылке из /invite: ФИО → дата рождения → пропуск mtgame → телефон →
    # согласие
    await harness.private(1, "/start reg_1")
    await harness.private(1, "Иванов Иван")
    await harness.private(1, "32.13.2000")  # некорректная дата — переспрашиваем
    await harness.private(1, "21.03.2005")
    await harness.press(1, Choice(flow="reg", value="skip").pack())
    await harness.private(1, "12345")  # некорректный телефон — переспрашиваем
    await harness.private(1, "8 (999) 123-45-67")
    await harness.press(1, Choice(flow="reg", value="consent").pack())
    texts = harness.session.texts_to(1)
    assert ru.CLUB_INFO in texts and ru.REG_ASK_BIRTH in texts
    assert texts[-1] == ru.REG_DONE.format(name="Иванов Иван")
    async with uow_factory() as uow:
        user = await uow.users.get_by_tg_id(1)
        assert user is not None and user.full_name == "Иванов Иван" and user.pd_consent_at
        assert user.phone == "+79991234567"

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

    # /squads → «Участники»: список состава с ролями; не админу кнопка не отвечает
    await harness.private(ADMIN_TG_ID, "/squads")
    members_btn = SquadPick(flow="squad_members", squad_id=squad.id).pack()
    await harness.press(ADMIN_TG_ID, members_btn)
    listing = harness.session.texts_to(ADMIN_TG_ID)[-1]
    assert listing.startswith("<b>Участники состава Взрослые</b> — 3:")
    assert (
        "\n1. " in listing and "user100 (не в боте) — тренер · ⚠️ не зарегистрирован(а)" in listing
    )
    assert "user999</a> — админ" in listing and "user2 (не в боте) — ⚠️" in listing
    await harness.press(PLAYER, members_btn)
    assert harness.session.texts_to(PLAYER) == []

    # /admins: обычный участник не видит список; админ назначает тренера вторым админом
    await harness.private(PLAYER, "/admins")
    assert harness.session.texts_to(PLAYER)[-1] == ru.NO_PERMISSION
    await harness.private(ADMIN_TG_ID, "/admins")
    await harness.press(ADMIN_TG_ID, AdminAction(action="add").pack())
    await harness.press(ADMIN_TG_ID, AdminAction(action="grant", user_id=coach_id).pack())
    assert (await ctx_of(services, COACH)).is_admin
    assert ru.ADMIN_GRANTED.format(
        name="user100 (не в боте)"  # назначен до того, как написал боту: ссылки нет
    ) in harness.session.texts_to(ADMIN_TG_ID)
    await harness.press(ADMIN_TG_ID, AdminAction(action="revoke", user_id=coach_id).pack())
    assert not (await ctx_of(services, COACH)).is_admin

    # /invite: тренеру — ссылка, игроку — отказ
    await harness.private(COACH, "/invite")
    assert f"https://t.me/pfbc_test_bot?start=reg_{squad.id}" in harness.session.texts_to(COACH)[-1]
    await harness.private(PLAYER, "/invite")
    assert harness.session.texts_to(PLAYER)[-1] == ru.NO_SQUADS_FOR_ACTION

    # /set: ключ состава → применяется к составу; глобальный — сразу; неизвестный — отказ
    admin = await ctx_of(services, ADMIN_TG_ID)
    await harness.private(ADMIN_TG_ID, "/set duty_count 3")
    assert (await services.settings.get_squad(admin, squad.id)).duty_count == 3
    await harness.private(ADMIN_TG_ID, "/set inactive_retention_days 180")
    assert (await services.settings.get_global(admin)).inactive_retention_days == 180
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
        await ctx_of(services, ADMIN_TG_ID), squad.id, "training_poll_time", "00:00"
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

    # в списке /attendance тренировка без опроса помечена
    await harness.private(COACH, "/attendance")
    buttons = [
        b.text
        for m in harness.session.requests
        if isinstance(m, SendMessage) and m.text == ru.ATTENDANCE_TRAININGS
        for row in m.reply_markup.inline_keyboard  # type: ignore[union-attr]
        for b in row
    ]
    assert buttons and all(b.endswith(ru.ATTENDANCE_NO_POLL_MARK) for b in buttons)

    # сводка по тренировке без опроса: ошибка после ответа на нажатие — сообщением, не тишиной
    await harness.press(COACH, ItemAction(kind="tr", action="att", item_id=training.id).pack())
    assert harness.session.texts_to(COACH)[-1] == ru.ATTENDANCE_NO_POLL

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


async def test_admin_becomes_member_by_bind_or_start(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """Админу бот доступен и без членства, но в состав он попадает как участник группы."""
    await bind(harness)  # привязавший группу админ — её участник
    admin = await ctx_of(services, ADMIN_TG_ID)
    assert admin.is_admin and admin.is_member

    # админ вышел и вернулся, пока бот этого не видел: в группе не писал — находится по /start
    await harness.member(ADMIN_TG_ID, joined=False)
    admin = await ctx_of(services, ADMIN_TG_ID)
    assert admin.is_admin and not admin.is_member
    notifier.chat_members[GROUP_ID] = {ADMIN_TG_ID}
    await harness.private(ADMIN_TG_ID, "/start")
    admin = await ctx_of(services, ADMIN_TG_ID)
    assert admin.is_admin and admin.is_member


async def test_review_with_default_time_and_tournament_links(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """/review: название и время по умолчанию кнопками → пост и опрос; /tournaments: админ
    привязывает матч к турниру."""
    await bind(harness)
    await harness.member(COACH, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach_id = (await ctx_of(services, COACH)).user.id
    await services.squads.set_role(admin, squad.id, coach_id, SquadRole.COACH, grant=True)
    day = (datetime.now(UTC) + timedelta(days=3)).date()
    training_at = local_to_utc(day, time(19, 0), MSK)
    await services.trainings.add_one_off(
        await ctx_of(services, COACH),
        TrainingCreate(squad_id=squad.id, starts_at=training_at, venue="Зал №5"),
    )

    await harness.private(COACH, "/review")
    await harness.press(COACH, Choice(flow="ev_title", value="default").pack())
    assert ru.EVENT_ASK_WHEN_REVIEW in harness.session.texts_to(COACH)[-1]
    await harness.press(COACH, Choice(flow="ev_when", value="default").pack())
    await harness.press(COACH, Choice(flow="ev_dur", value="60").pack())
    await harness.press(COACH, Choice(flow="ev_place", value="skip").pack())
    await harness.press(COACH, Choice(flow="ev_descr", value="skip").pack())
    await harness.press(COACH, Choice(flow="ev_pub", value="yes").pack())

    assert ru.EVENT_PUBLISHED_WITH_POLL in harness.session.texts_to(COACH)[-1]
    assert "Разбор игры: Разбор игры" in notifier.polls[-1][1]
    events = await services.events.upcoming([squad.id])
    assert [(e.type, e.starts_at) for e, _, _ in events] == [
        ("review", training_at - timedelta(hours=1))
    ]

    tournament = await services.events.create(
        admin,
        EventCreate(
            squad_id=squad.id,
            type="tournament",
            title="Кубок",
            starts_at=training_at + timedelta(days=1),
            is_season=True,
        ),
    )
    match = await services.events.create(
        admin,
        EventCreate(
            squad_id=squad.id,
            type="match",
            title="Финал",
            starts_at=training_at + timedelta(days=2),
            duration_minutes=120,
        ),
    )
    await harness.private(COACH, "/tournaments")  # турниры ведёт тренер
    assert harness.session.texts_to(COACH)[-1] == ru.TOURNAMENTS_HEADER
    await harness.private(ADMIN_TG_ID, "/tournaments")
    await harness.press(
        ADMIN_TG_ID, ItemAction(kind="trn", action="open", item_id=tournament.id).pack()
    )
    await harness.press(
        ADMIN_TG_ID,
        TournamentLink(tournament_id=tournament.id, match_id=match.id, link=1).pack(),
    )
    card = await services.events.tournament_card(admin, tournament.id)
    assert [m.tournament_id for m in card.matches] == [tournament.id]


async def test_tournament_season_and_review_offer_via_bot(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """/event_add турнира: дата неизвестна + «Сезон»; предложение разбора после матча → «Да»."""
    await bind(harness)
    await harness.member(COACH, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach_id = (await ctx_of(services, COACH)).user.id
    await services.squads.set_role(admin, squad.id, coach_id, SquadRole.COACH, grant=True)

    await harness.private(COACH, "/event_add")
    await harness.press(COACH, Choice(flow="ev_type", value="tournament").pack())
    await harness.private(COACH, "Лига ABL")
    assert harness.session.texts_to(COACH)[-1] == ru.TOURNAMENT_ASK_START
    await harness.press(COACH, Choice(flow="ev_tstart", value="unknown").pack())
    await harness.press(COACH, Choice(flow="ev_tlen", value="season").pack())
    assert harness.session.texts_to(COACH)[-1] == ru.TOURNAMENT_ASK_GAME_PRICE
    await harness.private(COACH, "15 000")
    await harness.press(COACH, Choice(flow="ev_place", value="skip").pack())
    await harness.press(COACH, Choice(flow="ev_descr", value="skip").pack())
    await harness.press(COACH, Choice(flow="ev_pub", value="yes").pack())
    assert ru.EVENT_PUBLISHED_WITH_POLL in harness.session.texts_to(COACH)[-1]
    assert "Лига ABL, сезон" in notifier.polls[-1][1]
    [league] = [t for t, _, _ in await services.events.tournaments(admin)]
    assert league.game_price == Decimal("15000.00")

    # прошёл матч → тренеру предложение → «Да» → время по умолчанию → «Да, …»
    day = (datetime.now(UTC) + timedelta(days=3)).date()
    training_at = local_to_utc(day, time(19, 0), MSK)
    await services.trainings.add_one_off(
        await ctx_of(services, COACH),
        TrainingCreate(squad_id=squad.id, starts_at=training_at, venue="Зал №5"),
    )
    played = datetime.now(UTC) + timedelta(hours=1)
    match = await services.events.create(
        admin, EventCreate(squad_id=squad.id, type="match", title="Финал", starts_at=played)
    )
    assert await services.events.offer_reviews(now=played + timedelta(hours=2)) == 1
    await harness.press(COACH, f"review:yes:{match.id}")
    proposal = harness.session.texts_to(COACH)[-1]
    assert "Финал" in proposal and "Опубликовать в группе с опросом?" in proposal
    review_at = training_at - timedelta(hours=1)
    await harness.press(COACH, f"review:ok:{match.id}:{int(review_at.timestamp())}")
    assert "Разбор игры: Финал" in notifier.polls[-1][1]
    events = await services.events.upcoming([squad.id])
    assert [(e.type, e.starts_at) for e, _, _ in events if e.type == "review"] == [
        ("review", review_at)
    ]


async def test_match_add_starts_with_tournament_choice(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    await bind(harness)
    await harness.member(COACH, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach_id = (await ctx_of(services, COACH)).user.id
    await services.squads.set_role(admin, squad.id, coach_id, SquadRole.COACH, grant=True)
    league = await services.events.create(
        admin, EventCreate(squad_id=squad.id, type="tournament", title="ABL", is_season=True)
    )

    day = (datetime.now(UTC) + timedelta(days=10)).date()
    await harness.private(COACH, "/event_add")
    await harness.press(COACH, Choice(flow="ev_type", value="match").pack())
    assert harness.session.texts_to(COACH)[-1] == ru.MATCH_ASK_TOURNAMENT
    await harness.press(COACH, Choice(flow="ev_trn", value=str(league.id)).pack())
    assert harness.session.texts_to(COACH)[-1] == ru.EVENT_ASK_TITLE
    await harness.private(COACH, "PFBC — Динамо")
    await harness.private(COACH, f"{day:%d.%m.%Y} 12:00")
    await harness.press(COACH, Choice(flow="ev_dur", value="120").pack())
    await harness.press(COACH, Choice(flow="ev_place", value="skip").pack())
    await harness.press(COACH, Choice(flow="ev_descr", value="skip").pack())
    await harness.press(COACH, Choice(flow="ev_pub", value="yes").pack())

    assert "🏆 Турнир: ABL" in notifier.to(GROUP_ID)[-1].text
    assert "(турнир «ABL»)" in notifier.polls[-1][1]
    [(match, _, _)] = await services.events.upcoming([squad.id])
    assert match.tournament_id == league.id


async def test_cancel_removes_question_without_reply(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """«Отмена» в вопросе подтверждения: вопрос удаляется, сообщения «Отменено» нет —
    его легко принять за отмену самого события."""
    await bind(harness)
    await harness.member(COACH, joined=True)
    for data in (
        ItemAction(kind="confirm", action="no", item_id=0).pack(),  # тренировка, расписание
        "duty:no",  # дежурства
        "review:no:1",  # предложение разбора
    ):
        sent_before = len(harness.session.texts_to(COACH))
        deleted_before = sum(isinstance(r, DeleteMessage) for r in harness.session.requests)
        await harness.press(COACH, data)
        assert len(harness.session.texts_to(COACH)) == sent_before, data
        deleted = sum(isinstance(r, DeleteMessage) for r in harness.session.requests)
        assert deleted == deleted_before + 1, data


async def test_game_pay_with_suggested_amount_and_new_details(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """/game_pay: игра турнира → сумма «стоимость ÷ игроки» → сутки → свой телефон → банк →
    предпросмотр → публикация поста и опроса «Оплатил»; реквизиты запоминаются."""
    await bind(harness)
    for tg_id in (COACH, 1, 2):
        await harness.member(tg_id, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach = await ctx_of(services, COACH)
    await services.squads.set_role(admin, squad.id, coach.user.id, SquadRole.COACH, grant=True)
    await services.registration.update_field(coach.user.id, "phone", "8 999 123 45 67")

    cup = await services.events.create(
        admin,
        EventCreate(
            squad_id=squad.id,
            type="tournament",
            title="Кубок",
            is_season=True,
            game_price=Decimal("15000"),
        ),
    )
    match = await services.events.create(
        admin,
        EventCreate(
            squad_id=squad.id,
            type="match",
            title="Финал",
            starts_at=datetime.now(UTC) + timedelta(days=3),
            tournament_id=cup.id,
            notify_group=True,
        ),
    )
    for tg_id in (1, 2):  # оба — «Буду (игрок)»
        await services.polls.record_answer("poll-1", tg_id, [1])

    await harness.private(COACH, "/game_pay")
    await harness.press(COACH, ItemAction(kind="gp", action="match", item_id=match.id).pack())
    await harness.press(COACH, Choice(flow="gp_amount", value="suggested").pack())
    await harness.press(COACH, Choice(flow="gp_deadline", value="day").pack())
    assert harness.session.texts_to(COACH)[-1].startswith(ru.GAME_PAYMENT_ASK_COLLECTOR)
    await harness.press(COACH, Choice(flow="gp_collector", value=str(coach.user.id)).pack())
    await harness.press(COACH, Choice(flow="pd_method", value="phone").pack())
    await harness.press(COACH, Choice(flow="pd_phone", value="mine").pack())
    await harness.private(COACH, "Сбербанк")
    preview = harness.session.texts_to(COACH)[-1]
    assert "Сумма: 7 500 ₽" in preview and "+79991234567" in preview and "Кубок" in preview
    await harness.press(COACH, Choice(flow="gp_pub", value="yes").pack())

    assert notifier.polls[-1][1] == "💰 Оплата игры «Финал»: 7 500 ₽"
    assert "Сумма: 7 500 ₽" in notifier.to(GROUP_ID)[-1].text
    [saved] = await services.game_payments.details_of(coach.user.id)
    assert saved.value == "+79991234567" and saved.bank == "Сбербанк"


async def test_permissions_admin_toggles_role(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """/permissions: админ выключает у капитана «Собрания» — /event_add капитану недоступен;
    «Сбросить по умолчанию» — снова доступен (в выборе типа — только «Собрание»). Сбор денег
    капитану не включить (🔒)."""
    await bind(harness)
    await harness.member(COACH, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    captain_id = (await ctx_of(services, COACH)).user.id
    await services.squads.set_role(admin, squad.id, captain_id, SquadRole.CAPTAIN, grant=True)

    await harness.private(COACH, "/permissions")
    assert harness.session.texts_to(COACH)[-1] == ru.NO_PERMISSION

    await harness.private(ADMIN_TG_ID, "/permissions")
    await harness.press(ADMIN_TG_ID, PermAction(action="role", role="captain").pack())
    await harness.press(
        ADMIN_TG_ID, PermAction(action="toggle", role="captain", perm="meetings").pack()
    )
    await harness.private(COACH, "/event_add")
    assert harness.session.texts_to(COACH)[-1] == ru.NO_SQUADS_FOR_ACTION

    await harness.press(ADMIN_TG_ID, PermAction(action="reset", role="captain").pack())
    await harness.private(COACH, "/event_add")
    assert harness.session.texts_to(COACH)[-1] == ru.EVENT_ASK_TYPE  # шаг — как у всех
    types = harness.session.requests[-1].reply_markup.inline_keyboard  # type: ignore[attr-defined]
    assert [b.text for row in types for b in row] == ["Собрание"]
    await harness.press(COACH, Choice(flow="ev_type", value="meeting").pack())
    assert harness.session.texts_to(COACH)[-1] == ru.EVENT_ASK_TITLE
    await harness.press(COACH, Choice(flow="ev_type", value="match").pack())  # подделка
    assert harness.session.texts_to(COACH)[-1] == ru.EVENT_ASK_TITLE  # ничего не произошло

    await harness.press(
        ADMIN_TG_ID, PermAction(action="locked", role="captain", perm="game_payments").pack()
    )
    assert not (await ctx_of(services, COACH)).can(squad.id, Permission.GAME_PAYMENTS)


async def test_forged_buttons_are_refused(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """Кнопки админки и /invite с подделанными данными: игрок не получает ни ссылки, ни
    списков — только отказ."""
    await bind(harness)
    await harness.member(PLAYER, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    sent_before = len(harness.session.texts_to(PLAYER))
    for data in (
        SquadPick(flow="invite", squad_id=squad.id).pack(),
        SquadPick(flow="roles", squad_id=squad.id).pack(),
        SquadPick(flow="admin_add", squad_id=squad.id).pack(),
        SquadPick(flow="settings_show", squad_id=squad.id).pack(),
        Choice(flow="settings_show", value="global").pack(),
        RoleAction(squad_id=squad.id, user_id=1, action="open").pack(),
        # выбор состава в сценариях тренера и капитана — у игрока этих прав нет
        *(
            SquadPick(flow=flow, squad_id=squad.id).pack()
            for flow in ("schedule", "trainings", "tr_add", "poll", "ev_add", "ev_review", "duty")
        ),
        *(SquadPick(flow=flow, squad_id=squad.id).pack() for flow in ("gp", "rc")),
    ):
        await harness.press(PLAYER, data)
    assert set(harness.session.texts_to(PLAYER)[sent_before:]) <= {ru.NO_PERMISSION}
    # и ни один диалог (добавление тренировки, события) не начался
    key = StorageKey(bot_id=harness.bot.id, chat_id=PLAYER, user_id=PLAYER)
    assert await harness.dp.storage.get_state(key) is None


async def test_payment_block_only_for_coach(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """/attendance по матчу: «кто придёт» видят все, блок оплаты — только тренер и админ."""
    await bind(harness)
    for tg_id in (COACH, PLAYER, 3):
        await harness.member(tg_id, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach = await ctx_of(services, COACH)
    await services.squads.set_role(admin, squad.id, coach.user.id, SquadRole.COACH, grant=True)
    captain_id = (await ctx_of(services, 3)).user.id
    await services.squads.set_role(admin, squad.id, captain_id, SquadRole.CAPTAIN, grant=True)
    coach = await ctx_of(services, COACH)
    match = await services.events.create(
        coach,
        EventCreate(
            squad_id=squad.id,
            type="match",
            title="Финал",
            starts_at=datetime.now(UTC) + timedelta(days=3),
            notify_group=True,
        ),
    )
    await services.game_payments.add_details(
        coach, PaymentDetails(method=PaymentMethod.PHONE, value="+79991234567", bank="Сбер")
    )
    await services.game_payments.create(
        coach,
        GamePaymentCreate(
            match_event_id=match.id,
            collector_user_id=coach.user.id,
            amount=Decimal("1000"),
            details=PaymentDetails(method=PaymentMethod.PHONE, value="+79991234567", bank="Сбер"),
        ),
    )
    for tg_id, has_block in ((COACH, True), (PLAYER, False), (3, False)):
        await harness.press(tg_id, ItemAction(kind="ev", action="att", item_id=match.id).pack())
        summary = harness.session.texts_to(tg_id)[-1]
        assert "Финал" in summary
        assert ("Оплата:" in summary) is has_block, tg_id


async def test_command_mid_dialog_exits_it(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """Команда посреди диалога — не ввод: «/game_pay» не станет названием события."""
    await bind(harness)
    await harness.member(COACH, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach_id = (await ctx_of(services, COACH)).user.id
    await services.squads.set_role(admin, squad.id, coach_id, SquadRole.COACH, grant=True)

    await harness.private(COACH, "/event_add")
    await harness.press(COACH, Choice(flow="ev_type", value="meeting").pack())
    assert harness.session.texts_to(COACH)[-1] == ru.EVENT_ASK_TITLE
    await harness.private(COACH, "/help")  # посреди ввода названия
    assert "Ваши функции" in harness.session.texts_to(COACH)[-1]
    await harness.private(COACH, "Собрание")  # диалога больше нет — это не название
    assert harness.session.texts_to(COACH)[-1] != ru.EVENT_ASK_WHEN


async def test_command_menu_follows_permissions(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """Меню команд у каждого своё: у игрока нет /game_pay, у тренера — есть."""
    # свои ID: диспетчер (и кеш меню в нём) общий на всю сессию тестов
    coach_tg, player_tg = 7701, 7702
    await bind(harness)
    for tg_id in (coach_tg, player_tg):
        await harness.member(tg_id, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach_id = (await ctx_of(services, coach_tg)).user.id
    await services.squads.set_role(admin, squad.id, coach_id, SquadRole.COACH, grant=True)

    def menu(chat_id: int) -> list[str]:
        sets = [
            r
            for r in harness.session.requests
            if isinstance(r, SetMyCommands)
            and isinstance(r.scope, BotCommandScopeChat)
            and r.scope.chat_id == chat_id
        ]
        return [c.command for c in sets[-1].commands] if sets else []

    await harness.private(player_tg, "/help")
    await harness.private(coach_tg, "/help")
    assert "game_pay" not in menu(player_tg) and "attendance" in menu(player_tg)
    assert {"game_pay", "event_add", "schedule"} <= set(menu(coach_tg))
    requests = len(harness.session.requests)
    await harness.private(player_tg, "/help")  # меню не менялось — повторно не ставится
    assert not any(isinstance(r, SetMyCommands) for r in harness.session.requests[requests:])


async def test_receipts_command_publishes_once_a_month(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """/receipts: срок по умолчанию → пост и опрос «Отправил»; повторно — только статус."""
    await bind(harness)
    for tg_id in (COACH, PLAYER):
        await harness.member(tg_id, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach = await ctx_of(services, COACH)
    await services.squads.set_role(admin, squad.id, coach.user.id, SquadRole.COACH, grant=True)

    await harness.private(PLAYER, "/receipts")  # игроку по умолчанию недоступно
    assert harness.session.texts_to(PLAYER)[-1] == ru.NO_SQUADS_FOR_ACTION

    await harness.private(COACH, "/receipts")
    assert harness.session.texts_to(COACH)[-1] == ru.RECEIPT_ASK_DEADLINE
    await harness.press(COACH, Choice(flow="rc_deadline", value="default").pack())
    assert "опубликован в группе" in harness.session.texts_to(COACH)[-1]
    assert notifier.polls[-1][1].endswith("отправили тренеру?")

    await harness.private(COACH, "/receipts")
    assert "уже опубликован" in harness.session.texts_to(COACH)[-1]
    assert len(notifier.polls) == 1

    # «Завершить сбор» → подтверждение → опрос закрыт, напоминаний нет
    current = await services.receipts.current(await ctx_of(services, COACH), squad.id)
    assert current is not None
    await harness.press(
        COACH, ItemAction(kind="rc", action="close", item_id=current.request_id).pack()
    )
    assert harness.session.texts_to(COACH)[-1] == ru.RECEIPT_CONFIRM_CLOSE
    await harness.press(
        COACH, ItemAction(kind="rc", action="close_ok", item_id=current.request_id).pack()
    )
    assert notifier.stopped_polls
    await harness.private(COACH, "/receipts")
    assert harness.session.texts_to(COACH)[-1].endswith(ru.PAYMENT_CLOSED_MARK)


async def test_profile_details_add_edit_delete(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    """/profile → Реквизиты: добавить, изменить, удалить (с подтверждением)."""
    await bind(harness)
    await harness.member(COACH, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    admin = await ctx_of(services, ADMIN_TG_ID)
    coach = await ctx_of(services, COACH)
    await services.squads.set_role(admin, squad.id, coach.user.id, SquadRole.COACH, grant=True)

    await harness.private(COACH, "/profile")
    await harness.press(COACH, Choice(flow="profile", value="payment").pack())
    assert ru.DETAILS_EMPTY in harness.session.texts_to(COACH)[-1]
    await harness.press(COACH, DetailsAction(action="add").pack())
    await harness.press(COACH, Choice(flow="pd_method", value="card").pack())
    await harness.private(COACH, "4111 1111 1111 1111")
    await harness.private(COACH, "Т-Банк")
    [card] = await services.game_payments.details_of(coach.user.id)
    assert card.bank == "Т-Банк"

    await harness.press(COACH, DetailsAction(action="edit", details_id=card.id).pack())
    await harness.press(COACH, Choice(flow="pd_method", value="phone").pack())
    await harness.private(COACH, "8 999 123 45 67")
    await harness.private(COACH, "Сбер")
    [edited] = await services.game_payments.details_of(coach.user.id)
    assert edited.id == card.id and edited.value == "+79991234567"
    assert "+79991234567" in harness.session.texts_to(COACH)[-1]  # телефон — слитно

    await harness.press(COACH, DetailsAction(action="del", details_id=card.id).pack())
    assert await services.game_payments.details_of(coach.user.id) != []  # только вопрос
    await harness.press(COACH, DetailsAction(action="delok", details_id=card.id).pack())
    assert await services.game_payments.details_of(coach.user.id) == []


async def test_admin_sees_member_details(
    harness: Harness, services: Services, notifier: FakeNotifier
) -> None:
    await bind(harness)
    await harness.member(PLAYER, joined=True)
    squad = await services.squads.squad_for_chat(GROUP_ID)
    assert squad is not None
    player = await ctx_of(services, PLAYER)
    await services.registration.update_field(player.user.id, "phone", "8 999 765 43 21")
    await harness.press(
        ADMIN_TG_ID, RoleAction(squad_id=squad.id, user_id=player.user.id, action="open").pack()
    )
    card = harness.session.texts_to(ADMIN_TG_ID)[-1]
    assert "Телефон: +79997654321" in card and "Регистрация: ⚠️ не пройдена" in card
    assert ru.CARD_NO_LINK in card  # без username и не писал боту — объясняем, почему без ссылки
