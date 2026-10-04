from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, CreatedAtMixin, IdMixin, str_enum
from pfbc_bot.domain.enums import MatchSource


class Match(IdMixin, CreatedAtMixin, Base):
    """Матч ABL (этап 2: парсер ablforpeople.com + ручной ввод как фолбэк)."""

    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("squad_id", "external_id"),)

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    opponent: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime]
    venue: Mapped[str | None] = mapped_column(String(255))
    stage: Mapped[str | None] = mapped_column(String(50))  # квалификация / круг / плей-офф
    division: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[MatchSource] = mapped_column(str_enum(MatchSource))
    external_id: Mapped[str | None] = mapped_column(String(100))
    manual_override: Mapped[bool] = mapped_column(default=False)
    raw_payload: Mapped[dict[str, Any] | None]
