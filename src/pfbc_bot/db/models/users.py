from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, CreatedAtMixin, IdMixin, str_enum
from pfbc_bot.domain.enums import UserStatus


class User(IdMixin, CreatedAtMixin, Base):
    """Пользователь Telegram и его профиль в системе клуба."""

    __tablename__ = "users"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))  # имя из Telegram
    full_name: Mapped[str | None] = mapped_column(String(200))  # ФИО из анкеты
    birth_date: Mapped[date | None]
    mtgame_url: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(20))  # +79991234567, по желанию
    # реквизиты для сбора денег (тренер): {"method": "phone"|"card", "value": ..., "bank": ...}
    payment_details: Mapped[dict[str, Any] | None]
    pd_consent_at: Mapped[datetime | None]
    guardian_consent: Mapped[bool] = mapped_column(default=False)
    is_admin: Mapped[bool] = mapped_column(default=False)
    dm_available: Mapped[bool] = mapped_column(default=False)
    status: Mapped[UserStatus] = mapped_column(
        str_enum(UserStatus), default=UserStatus.UNREGISTERED, index=True
    )
    inactive_since: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
