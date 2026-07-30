"""Отправка уведомлений в Telegram Bot API."""

from collections.abc import Mapping
from typing import Any

import httpx

from lead_intake.models import Lead
from lead_intake.services.notifications.base import NotificationResult


class TelegramNotifier:
    """Канал доставки заявок в один или несколько Telegram-чатов."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Сохранить расшифрованную конфигурацию Telegram-канала."""
        bot_token = config.get("bot_token")
        chat_ids = config.get("chat_ids")
        if not isinstance(bot_token, str) or not bot_token:
            raise ValueError("Telegram bot_token is required")
        if not isinstance(chat_ids, list) or not all(
            isinstance(chat_id, str) and chat_id for chat_id in chat_ids
        ):
            raise ValueError("Telegram chat_ids must be a non-empty list of strings")

        self._bot_token = bot_token
        self._chat_ids = chat_ids

    def send(self, lead: Lead) -> NotificationResult:
        """Отправить текст заявки во все настроенные Telegram-чаты."""
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        text = _format_lead_message(lead)
        try:
            with httpx.Client(timeout=10.0) as client:
                for chat_id in self._chat_ids:
                    response = client.post(url, json={"chat_id": chat_id, "text": text})
                    response.raise_for_status()
        except httpx.HTTPStatusError as error:
            return _status_error_result(error)
        except httpx.TimeoutException as error:
            return NotificationResult(
                success=False,
                message="Telegram delivery failed",
                reason="telegram_timeout",
                exception_type=type(error).__name__,
            )
        except httpx.NetworkError as error:
            return NotificationResult(
                success=False,
                message="Telegram delivery failed",
                reason="telegram_network_error",
                exception_type=type(error).__name__,
            )
        except httpx.RequestError as error:
            return NotificationResult(
                success=False,
                message="Telegram delivery failed",
                reason="telegram_request_error",
                exception_type=type(error).__name__,
            )

        return NotificationResult(success=True)


def _status_error_result(error: httpx.HTTPStatusError) -> NotificationResult:
    """Преобразовать HTTP-ответ Telegram в безопасный код для логов."""
    provider_status = error.response.status_code
    reason = _telegram_error_reason(
        provider_status,
        _response_description(error.response),
    )
    return NotificationResult(
        success=False,
        message="Telegram delivery failed",
        reason=reason,
        provider_status=provider_status,
        exception_type=type(error).__name__,
    )


def _response_description(response: httpx.Response) -> str:
    """Извлечь описание ошибки только для внутренней классификации."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    description = payload.get("description") if isinstance(payload, dict) else None
    return description if isinstance(description, str) else ""


def _telegram_error_reason(provider_status: int, description: str) -> str:
    """Вернуть безопасный код Telegram-ошибки без сохранения её текста."""
    normalized_description = description.casefold()
    if "chat not found" in normalized_description:
        return "telegram_chat_not_found"
    if "bot was blocked by the user" in normalized_description:
        return "telegram_bot_blocked_by_user"
    if "user is deactivated" in normalized_description:
        return "telegram_user_deactivated"
    if "group chat was upgraded to a supergroup chat" in normalized_description:
        return "telegram_chat_migrated"
    if (
        "bot is not a member" in normalized_description
        or "not enough rights to send" in normalized_description
    ):
        return "telegram_chat_write_forbidden"

    reason_by_status = {
        401: "telegram_unauthorized",
        403: "telegram_forbidden",
        429: "telegram_rate_limited",
    }
    if provider_status in reason_by_status:
        return reason_by_status[provider_status]
    if provider_status >= 500:
        return "telegram_provider_unavailable"
    if provider_status == 400:
        return "telegram_request_invalid"
    return "telegram_rejected"


def _format_lead_message(lead: Lead) -> str:
    """Сформировать текст уведомления без технических данных конфигурации."""
    name = lead.name or "Не указано"
    comment = lead.comment or "Не указан"
    return (
        f"Новая заявка #{lead.id}\n"
        f"Имя: {name}\n"
        f"Контакт: {lead.contact}\n"
        f"Источник: {lead.source}\n"
        f"Комментарий: {comment}"
    )
