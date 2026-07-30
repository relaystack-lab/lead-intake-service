"""Отправка уведомлений по электронной почте."""

import smtplib
import ssl
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Any

from lead_intake.models import Lead
from lead_intake.services.notifications.base import NotificationResult


class EmailNotifier:
    """Канал доставки заявок через SMTP с SSL или STARTTLS."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Сохранить расшифрованную SMTP-конфигурацию канала."""
        self._config = dict(config)

    def send(self, lead: Lead) -> NotificationResult:
        """Отправить письмо с данными заявки через настроенный SMTP-сервер."""
        try:
            message = self._build_message(lead)
            with self._create_client() as client:
                smtp_user = self._config.get("smtp_user")
                smtp_password = self._config.get("smtp_password")
                if isinstance(smtp_user, str) and smtp_user:
                    if not isinstance(smtp_password, str):
                        raise ValueError("SMTP password is required")
                    client.login(smtp_user, smtp_password)
                client.send_message(message)
        except smtplib.SMTPAuthenticationError as error:
            return _failure_result("smtp_auth_failed", error)
        except smtplib.SMTPRecipientsRefused as error:
            return _failure_result("smtp_recipient_rejected", error)
        except smtplib.SMTPConnectError as error:
            return _failure_result("smtp_connection_failed", error)
        except smtplib.SMTPResponseException as error:
            return _failure_result("smtp_server_rejected", error)
        except smtplib.SMTPServerDisconnected as error:
            return _failure_result("smtp_connection_failed", error)
        except ssl.SSLError as error:
            return _failure_result("smtp_tls_failed", error)
        except TimeoutError as error:
            return _failure_result("smtp_timeout", error)
        except OSError as error:
            return _failure_result("smtp_connection_failed", error)
        except smtplib.SMTPException as error:
            return _failure_result("smtp_protocol_error", error)
        except ValueError as error:
            return _failure_result("channel_configuration_invalid", error)

        return NotificationResult(success=True)

    def _create_client(self) -> smtplib.SMTP:
        """Открыть SMTP-соединение в режиме SSL или STARTTLS."""
        smtp_host = self._required_text("smtp_host")
        smtp_port = self._config.get("smtp_port")
        tls_mode = self._config.get("tls_mode")
        if not isinstance(smtp_port, int):
            raise ValueError("SMTP port is required")
        if tls_mode == "ssl":
            return smtplib.SMTP_SSL(
                smtp_host,
                smtp_port,
                context=ssl.create_default_context(),
            )
        if tls_mode == "starttls":
            client = smtplib.SMTP(smtp_host, smtp_port)
            client.starttls(context=ssl.create_default_context())
            return client
        raise ValueError("Unsupported SMTP TLS mode")

    def _build_message(self, lead: Lead) -> EmailMessage:
        """Сформировать MIME-письмо для одного или нескольких получателей."""
        recipients = self._config.get("recipients")
        if not isinstance(recipients, list) or not all(
            isinstance(recipient, str) and recipient for recipient in recipients
        ):
            raise ValueError("Email recipients are required")

        message = EmailMessage()
        message["From"] = self._required_text("from_address")
        message["To"] = ", ".join(recipients)
        subject_template = self._config.get("subject_template", "Новая заявка #{id}")
        if not isinstance(subject_template, str):
            raise ValueError("Email subject template must be a string")
        message["Subject"] = subject_template.format(id=lead.id, source=lead.source)
        message.set_content(
            "\n".join(
                [
                    f"Заявка: #{lead.id}",
                    f"Имя: {lead.name or 'Не указано'}",
                    f"Контакт: {lead.contact}",
                    f"Источник: {lead.source}",
                    f"Комментарий: {lead.comment or 'Не указан'}",
                ]
            )
        )
        return message

    def _required_text(self, field_name: str) -> str:
        """Получить обязательное непустое строковое поле конфигурации."""
        value = self._config.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Email {field_name} is required")
        return value


def _failure_result(reason: str, error: Exception) -> NotificationResult:
    """Вернуть безопасный результат SMTP-сбоя без текста исключения."""
    provider_status = (
        error.smtp_code if isinstance(error, smtplib.SMTPResponseException) else None
    )
    return NotificationResult(
        success=False,
        message="Email delivery failed",
        reason=reason,
        provider_status=provider_status,
        exception_type=type(error).__name__,
    )
