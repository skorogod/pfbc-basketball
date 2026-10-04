from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pfbc_bot.db.base import Base, CreatedAtMixin, IdMixin, str_enum
from pfbc_bot.domain.enums import MembershipStatus, SquadRole, TopicPurpose


class Squad(IdMixin, CreatedAtMixin, Base):
    """Состав клуба (возрастная команда)."""

    __tablename__ = "squads"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    age_group: Mapped[str | None] = mapped_column(String(50))
    is_youth: Mapped[bool] = mapped_column(default=False)  # детский состав: согласие представителя
    abl_team_ref: Mapped[str | None] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    is_active: Mapped[bool] = mapped_column(default=True)


class SquadMember(IdMixin, Base):
    """Членство пользователя в составе — отражение членства в Telegram-группе состава."""

    __tablename__ = "squad_members"
    __table_args__ = (
        UniqueConstraint("user_id", "squad_id"),
        Index("ix_squad_members_user_status", "user_id", "status"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    status: Mapped[MembershipStatus] = mapped_column(
        str_enum(MembershipStatus), default=MembershipStatus.ACTIVE
    )
    first_joined_at: Mapped[datetime]  # не перезаписывается при повторном вступлении
    joined_at: Mapped[datetime]
    left_at: Mapped[datetime | None]
    last_verified_at: Mapped[datetime | None]


class SquadRoleAssignment(IdMixin, Base):
    """Роль пользователя в составе. Действует, только пока членство активно."""

    __tablename__ = "squad_roles"
    __table_args__ = (UniqueConstraint("user_id", "squad_id", "role"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    role: Mapped[SquadRole] = mapped_column(str_enum(SquadRole))


class SquadChat(IdMixin, Base):
    """Telegram-группа состава (одна группа на состав)."""

    __tablename__ = "squad_chats"

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    title: Mapped[str | None] = mapped_column(String(255))


class SquadTopic(IdMixin, Base):
    """Привязка топика группы к назначению (тренировки, игры, поздравления...)."""

    __tablename__ = "squad_topics"
    __table_args__ = (UniqueConstraint("squad_id", "purpose"),)

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"))
    purpose: Mapped[TopicPurpose] = mapped_column(str_enum(TopicPurpose))
    message_thread_id: Mapped[int | None]
