from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, CreatedAtMixin, IdMixin, str_enum
from pfbc_bot.domain.enums import GreetingKind, GreetingStatus


class Greeting(IdMixin, CreatedAtMixin, Base):
    """Шаблон поздравления с ДР или запланированное поздравление с праздником."""

    __tablename__ = "greetings"

    kind: Mapped[GreetingKind] = mapped_column(str_enum(GreetingKind), index=True)
    text: Mapped[str] = mapped_column(Text)
    media_file_id: Mapped[str | None] = mapped_column(String(255))
    target_squad_ids: Mapped[list[Any] | None]  # None — все составы
    send_at: Mapped[datetime | None]
    status: Mapped[GreetingStatus | None] = mapped_column(str_enum(GreetingStatus))
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
