from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from pfbc_bot.domain.enums import EventType
from pfbc_bot.schemas.base import DTO


class EventCreate(BaseModel):
    squad_id: int
    type: EventType
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    duration_minutes: int = Field(default=90, ge=15, le=1440)
    place: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    notify_group: bool = False
    match_id: int | None = None


class EventDTO(DTO):
    id: int
    squad_id: int
    type: EventType
    title: str
    starts_at: datetime
    duration_minutes: int
    place: str | None
    description: str | None
    notify_group: bool

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(minutes=self.duration_minutes)


class ScheduleConflict(DTO):
    """Пересечение по времени с уже запланированным мероприятием состава."""

    kind: Literal["training", "match", "event"]
    title: str
    starts_at: datetime
    ends_at: datetime
