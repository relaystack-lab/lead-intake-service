"""ORM-модели хранения заявок, событий и каналов уведомлений."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс декларативных моделей SQLAlchemy."""


class LeadStatus(StrEnum):
    """Допустимые состояния заявки."""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class NotificationChannelType(StrEnum):
    """Поддерживаемые типы каналов уведомлений."""

    TELEGRAM = "telegram"
    EMAIL = "email"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    """Вернуть строковые значения enum для хранения в базе данных."""
    return [item.value for item in enum_class]


class Lead(Base):
    """Заявка, полученная из одного из внешних источников."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    contact: Mapped[str] = mapped_column(String(255), index=True)
    comment: Mapped[str] = mapped_column(Text, default="", server_default="")
    source: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[LeadStatus] = mapped_column(
        Enum(
            LeadStatus,
            name="lead_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        default=LeadStatus.NEW,
        server_default=LeadStatus.NEW.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class Event(Base):
    """Неизменяемая запись о событии, связанном с заявкой."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"),
        index=True,
    )
    type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class NotificationChannel(Base):
    """Настройка одного канала доставки уведомлений."""

    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[NotificationChannelType] = mapped_column(
        Enum(
            NotificationChannelType,
            name="notification_channel_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="1",
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_revision: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
    )
    verified_config_revision: Mapped[int | None] = mapped_column(Integer)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
