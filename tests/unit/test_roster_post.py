"""«Состав на матч»: в группе — упоминания, в предпросмотре в личке — ссылки, которые там
работают (Telegram прячет tg://user в личке, если человек не писал боту)."""

from datetime import UTC, datetime

from pfbc_bot.db.models import User
from pfbc_bot.domain.enums import EventType, UserStatus
from pfbc_bot.schemas.events import EventDTO
from pfbc_bot.services.game_payments import format_roster_post

MSK = "Europe/Moscow"
MATCH = EventDTO(
    id=1,
    squad_id=1,
    type=EventType.MATCH,
    title="PFBC — Динамо",
    starts_at=datetime(2026, 10, 17, 18, 50, tzinfo=UTC),
    duration_minutes=90,
    place="ФОК",
    description="Москвич, тур 7",
    notify_group=True,
)


def user(uid: int, name: str, *, username: str | None = None, in_bot: bool = False) -> User:
    return User(
        id=uid,
        tg_user_id=uid,
        username=username,
        first_name=name,
        full_name=None,
        birth_date=None,
        mtgame_url=None,
        pd_consent_at=None,
        is_admin=False,
        dm_available=in_bot,
        status=UserStatus.UNREGISTERED,
    )


PLAYERS = [
    user(1, "Аня", in_bot=True),
    user(2, "Боря", username="borya"),
    user(3, "Вова"),  # нет username и не писал боту
]


def test_group_post_mentions_everyone_with_date_without_place() -> None:
    post = format_roster_post(MATCH, PLAYERS, MSK)
    assert post.startswith("🏀 <b>Состав на матч</b>\n<b>PFBC — Динамо</b>\n🗓 сб, 17.10 · 21:50\n")
    assert "ФОК" not in post and "Москвич" not in post
    for uid in (1, 2, 3):
        assert f'<a href="tg://user?id={uid}">' in post


def test_dm_preview_uses_links_that_work_in_private_chat() -> None:
    post = format_roster_post(MATCH, PLAYERS, MSK, in_group=False)
    assert '1. <a href="tg://user?id=1">Аня</a>' in post
    assert '2. <a href="https://t.me/borya">Боря</a>' in post  # username — ссылка всегда
    assert "3. Вова" in post and "не в боте" not in post
