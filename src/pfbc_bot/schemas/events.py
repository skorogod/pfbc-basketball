from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from pfbc_bot.domain.enums import EventType
from pfbc_bot.schemas.base import DTO


class EventCreate(BaseModel):
    squad_id: int
    type: EventType
    title: str = Field(min_length=1, max_length=200)
    # у турнира дата старта может быть ещё неизвестна; у остальных событий — обязательна
    starts_at: datetime | None = None
    duration_minutes: int = Field(default=90, ge=15, le=1440)
    # турнир: число дней или сезон (лига) — точная продолжительность не нужна
    duration_days: int | None = Field(default=None, ge=1, le=366)
    is_season: bool = False
    game_price: Decimal | None = Field(default=None, gt=0, lt=10_000_000)  # турнир
    division: str | None = Field(default=None, max_length=100)  # турнир: дивизион лиги
    place: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    notify_group: bool = False
    match_id: int | None = None
    tournament_id: int | None = None  # матч в рамках турнира

    @model_validator(mode="after")
    def _period(self) -> Self:
        if self.type == EventType.TOURNAMENT:
            if self.is_season == (self.duration_days is not None):
                raise ValueError("Турнир: укажите число дней или сезон")
        elif self.starts_at is None:
            raise ValueError("Укажите дату и время начала")
        elif self.duration_days is not None or self.is_season:
            raise ValueError("Дни и сезон указываются только у турнира")
        if self.game_price is not None and self.type != EventType.TOURNAMENT:
            raise ValueError("Стоимость игры указывается только у турнира")
        if self.division is not None and self.type != EventType.TOURNAMENT:
            raise ValueError("Дивизион указывается только у турнира")
        if self.tournament_id is not None and self.type != EventType.MATCH:
            raise ValueError("К турниру привязывается только матч")
        return self


class EventDTO(DTO):
    id: int
    squad_id: int
    type: EventType
    title: str
    starts_at: datetime | None
    duration_minutes: int
    duration_days: int | None = None
    is_season: bool = False
    game_price: Decimal | None = None
    division: str | None = None
    place: str | None
    description: str | None
    notify_group: bool
    tournament_id: int | None = None
    tournament_title: str | None = None  # для матча — название его турнира
    # результат публикации в группе (только у только что созданного/перенесённого события)
    posted: bool | None = None
    poll_posted: bool | None = None

    @property
    def ends_at(self) -> datetime | None:
        """Конец события; у турнира без даты или сезонного — неизвестен (None)."""
        if self.starts_at is None:
            return None
        if self.type == EventType.TOURNAMENT:
            if self.duration_days is None:
                return None
            return self.starts_at + timedelta(days=self.duration_days)
        return self.starts_at + timedelta(minutes=self.duration_minutes)


class ScheduleConflict(DTO):
    """Пересечение по времени с уже запланированным мероприятием состава."""

    kind: Literal["training", "match", "event"]
    title: str
    starts_at: datetime
    ends_at: datetime


class TournamentCard(BaseModel):
    """Турнир и матчи для привязки: уже привязанные к нему и свободные."""

    tournament: EventDTO
    squad_name: str
    tz: str
    matches: list[EventDTO]


class ReviewProposal(BaseModel):
    """Предложение назначить разбор после матча: матч и время разбора по умолчанию."""

    match: EventDTO
    tz: str
    default_at: datetime | None
