from datetime import date, time

import pytest
from pydantic import ValidationError

from pfbc_bot import callbacks
from pfbc_bot.domain.enums import SquadRole, UserStatus
from pfbc_bot.schemas.settings import SquadSettings
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.schemas.users import RegistrationData, UserDTO, validate_mtgame_url
from pfbc_bot.services.errors import DomainError
from pfbc_bot.services.settings import apply_override, has_setting


@pytest.mark.parametrize(
    "url",
    ["https://mtgame.ru/player/123", "mtgame.ru/player/123", "https://www.mtgame.ru/p/1"],
)
def test_mtgame_url_accepts_profile_links(url: str) -> None:
    assert validate_mtgame_url(url).startswith("http")


@pytest.mark.parametrize(
    "url",
    ["https://mtgame.ru/", "https://evil-mtgame.ru/player/1", "https://example.com/mtgame.ru/1"],
)
def test_mtgame_url_rejects_other_links(url: str) -> None:
    with pytest.raises(ValueError):
        validate_mtgame_url(url)


def test_registration_requires_consent_and_name() -> None:
    with pytest.raises(ValidationError):
        RegistrationData(full_name="Иван", pd_consent=False)
    with pytest.raises(ValidationError):
        RegistrationData(full_name="1", pd_consent=True)


def test_registration_parses_birth_date_and_normalizes_name() -> None:
    data = RegistrationData(full_name="  Иванов   Иван ", birth_date="21.03.2005", pd_consent=True)
    assert data.full_name == "Иванов Иван"
    assert data.birth_date == date(2005, 3, 21)
    iso = RegistrationData(full_name="Иван", birth_date="2005-03-21", pd_consent=True)
    assert iso.birth_date == date(2005, 3, 21)


def test_registration_rejects_future_birth_date() -> None:
    with pytest.raises(ValidationError):
        RegistrationData(full_name="Иван", birth_date=date(2999, 1, 1), pd_consent=True)


def test_squad_settings_override_and_validation() -> None:
    overrides = apply_override(SquadSettings, {}, "training_poll_time", "08:30")
    overrides = apply_override(SquadSettings, overrides, "features.duty", "false")
    settings = SquadSettings.model_validate(overrides)
    assert settings.training_poll_time == time(8, 30)
    assert settings.features.duty is False
    assert settings.features.birthdays is True
    with pytest.raises(DomainError):
        apply_override(SquadSettings, {}, "duty_count", "0")
    with pytest.raises(DomainError):
        apply_override(SquadSettings, {}, "unknown_key", "1")
    with pytest.raises(DomainError):
        apply_override(SquadSettings, {}, "features", "{}")  # группа целиком — не настройка


def test_setting_keys_and_stale_values() -> None:
    assert has_setting(SquadSettings, "duty_count")
    assert has_setting(SquadSettings, "features.birthdays")
    assert not has_setting(SquadSettings, "features.nope")
    assert not has_setting(SquadSettings, "registration_reminder_max")
    # устаревшие ключи, оставшиеся в БД, не ломают загрузку настроек
    stale = {"registration_reminder_max": 3, "features": {"registration_reminders": False}}
    assert SquadSettings.model_validate(stale).duty_count == 2


def test_user_context_roles() -> None:
    user = UserDTO(
        id=1,
        tg_user_id=10,
        username=None,
        first_name="Иван",
        full_name=None,
        birth_date=None,
        mtgame_url=None,
        pd_consent_at=None,
        is_admin=False,
        dm_available=False,
        status=UserStatus.UNREGISTERED,
    )
    ctx = UserContext(
        user=user,
        is_admin=False,
        roles={
            1: frozenset({SquadRole.PLAYER, SquadRole.CAPTAIN}),
            2: frozenset({SquadRole.PLAYER}),
        },
    )
    assert ctx.is_allowed
    assert ctx.has_role(1, SquadRole.CAPTAIN)
    assert not ctx.has_role(2, SquadRole.CAPTAIN, SquadRole.COACH)
    assert ctx.squads_with_roles(SquadRole.CAPTAIN) == [1]
    assert not UserContext(user=user, is_admin=False).is_allowed


def test_callback_pack_parse_roundtrip() -> None:
    data = callbacks.pack(callbacks.DUTY, callbacks.DUTY_PICK, 15, 42)
    parsed = callbacks.parse(data)
    assert (parsed.prefix, parsed.action, parsed.args) == ("duty", "pick", (15, 42))
    assert len(data.encode()) <= 64
