"""Общий контракт каналов уведомлений."""

from dataclasses import dataclass
from typing import Protocol

from lead_intake.models import Lead


@dataclass(frozen=True, slots=True)
class NotificationResult:
    """Результат одной попытки отправки в канал уведомлений."""

    success: bool
    message: str | None = None
    reason: str | None = None
    provider_status: int | None = None
    exception_type: str | None = None


class Notifier(Protocol):
    """Канал, способный отправить уведомление о заявке."""

    def send(self, lead: Lead) -> NotificationResult:
        """Отправить уведомление и вернуть безопасный результат попытки."""
