"""Фоновая доставка уведомлений по включённым каналам."""

import logging
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lead_intake.config import Settings
from lead_intake.models import Event, Lead, NotificationChannel, NotificationChannelType
from lead_intake.security import decrypt_channel_config, get_fernet
from lead_intake.services.notifications.base import NotificationResult, Notifier
from lead_intake.services.notifications.email import EmailNotifier
from lead_intake.services.notifications.telegram import TelegramNotifier

logger = logging.getLogger("lead_intake")


def dispatch_notifications(
    session_factory: sessionmaker[Session],
    settings: Settings,
    lead_id: int,
) -> None:
    """Отправить уведомления по всем включённым каналам для заявки."""
    session = session_factory()
    try:
        lead = session.get(Lead, lead_id)
        if lead is None:
            logger.error("Lead for notification dispatch was not found")
            return

        channels = list(
            session.scalars(
                select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
            )
        )
        for channel in channels:
            attempt_id = uuid4().hex
            result = _deliver_to_channel(channel, lead, settings)
            _log_delivery_failure(lead.id, channel, result, attempt_id)
            _record_result(session, lead.id, channel, result, attempt_id)
    finally:
        session.close()


def send_test_notification(
    settings: Settings,
    channel_type: NotificationChannelType,
    config: Mapping[str, Any],
) -> NotificationResult:
    """Проверить текущую конфигурацию через отдельного проверочного получателя."""
    test_lead = Lead(
        id=0,
        name="Проверка уведомлений",
        contact="Не требуется",
        comment="Проверочная отправка из административного интерфейса",
        source="admin",
    )
    return _deliver_config(channel_type, config, test_lead, settings)


def _deliver_to_channel(
    channel: NotificationChannel,
    lead: Lead,
    settings: Settings,
) -> NotificationResult:
    """Доставить заявку в один канал, изолировав сбой от остальных каналов."""
    return _deliver_config(channel.type, channel.config, lead, settings)


def _deliver_config(
    channel_type: NotificationChannelType,
    config: Mapping[str, Any],
    lead: Lead,
    settings: Settings,
) -> NotificationResult:
    """Доставить заявку по конфигурации канала без обращения к базе данных."""
    try:
        fernet = get_fernet(settings.secrets_key)
        decrypted_config = decrypt_channel_config(channel_type.value, config, fernet)
        notifier = _build_notifier(channel_type, decrypted_config)
        return notifier.send(lead)
    except (TypeError, ValueError) as error:
        return NotificationResult(
            success=False,
            message="Channel configuration is unavailable",
            reason="channel_configuration_invalid",
            exception_type=type(error).__name__,
        )
    except Exception as error:
        return NotificationResult(
            success=False,
            message="Channel delivery failed",
            reason="unexpected_delivery_error",
            exception_type=type(error).__name__,
        )


def _build_notifier(
    channel_type: NotificationChannelType,
    config: Mapping[str, Any],
) -> Notifier:
    """Создать адаптер для типа сохранённого канала."""
    if channel_type is NotificationChannelType.TELEGRAM:
        return TelegramNotifier(config)
    if channel_type is NotificationChannelType.EMAIL:
        return EmailNotifier(config)
    raise ValueError("Unsupported notification channel type")


def _record_result(
    session: Session,
    lead_id: int,
    channel: NotificationChannel,
    result: NotificationResult,
    attempt_id: str,
) -> None:
    """Записать безопасный результат доставки отдельной транзакцией."""
    event_type = "notification_sent" if result.success else "notification_failed"
    payload: dict[str, int | str] = {
        "channel_id": channel.id,
        "channel_type": channel.type.value,
    }
    if result.message is not None:
        payload["message"] = result.message

    try:
        session.add(Event(lead_id=lead_id, type=event_type, payload=payload))
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.error(
            "Notification event could not be recorded",
            extra={
                "event": "notification_event_record_failed",
                "category": "notification_delivery",
                "lead_id": lead_id,
                "channel_id": channel.id,
                "channel_type": channel.type.value,
                "attempt_id": attempt_id,
            },
        )


def _log_delivery_failure(
    lead_id: int,
    channel: NotificationChannel,
    result: NotificationResult,
    attempt_id: str,
) -> None:
    """Записать безопасную диагностику неуспешной доставки в JSONL-лог."""
    if result.success:
        return

    extra: dict[str, int | str] = {
        "event": "notification_delivery_failed",
        "category": "notification_delivery",
        "lead_id": lead_id,
        "channel_id": channel.id,
        "channel_type": channel.type.value,
        "attempt_id": attempt_id,
        "reason": result.reason or "delivery_failed",
    }
    if result.provider_status is not None:
        extra["provider_status"] = result.provider_status
    if result.exception_type is not None:
        extra["exception_type"] = result.exception_type
    logger.warning("Notification delivery failed", extra=extra)
