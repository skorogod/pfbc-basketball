from datetime import date, datetime, time

from pydantic import BaseModel, Field

from pfbc_bot.domain.enums import DutyStatus, TrainingSource, TrainingStatus
from pfbc_bot.schemas.base import DTO
from pfbc_bot.schemas.events import EventDTO


class ScheduleCreate(BaseModel):
    squad_id: int
    weekday: int = Field(ge=0, le=6)
    start_time: time
    duration_minutes: int = Field(ge=15, le=600)
    venue: str = Field(min_length=1, max_length=255)
    valid_from: date | None = None
    valid_to: date | None = None


class ScheduleDTO(DTO):
    id: int
    squad_id: int
    weekday: int
    start_time: time
    duration_minutes: int
    venue: str
    valid_from: date | None
    valid_to: date | None
    is_active: bool


class TrainingCreate(BaseModel):
    squad_id: int
    starts_at: datetime
    duration_minutes: int = Field(default=90, ge=15, le=600)
    venue: str = Field(min_length=1, max_length=255)


class TrainingDTO(DTO):
    id: int
    squad_id: int
    schedule_id: int | None
    starts_at: datetime
    duration_minutes: int
    venue: str
    status: TrainingStatus
    source: TrainingSource


class DutyAssignmentDTO(DTO):
    id: int
    training_id: int
    user_id: int
    status: DutyStatus


class AttendanceSummary(BaseModel):
    training: TrainingDTO
    by_option: dict[str, list[str]]  # вариант ответа → имена
    not_answered: list[str]


class EventPollSummary(BaseModel):
    event: EventDTO
    by_option: dict[str, list[str]]  # вариант ответа → имена
    not_answered: list[str]
