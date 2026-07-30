"""Проверки доставки уведомлений без реальных сетевых вызовов."""

import smtplib
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from lead_intake.config import Settings
from lead_intake.models import Event, Lead, NotificationChannel, NotificationChannelType
from lead_intake.security import create_fernet, encrypt_channel_config
from lead_intake.services.notifications.base import NotificationResult
from lead_intake.services.notifications.dispatcher import dispatch_notifications
from lead_intake.services.notifications.email import EmailNotifier
from lead_intake.services.notifications.telegram import (
    TelegramNotifier,
    _status_error_result,
)

API_HEADERS = {"X-API-Key": "test-api-key"}


class FakeHttpxResponse:
    """Минимальный успешный ответ HTTPX для проверки Telegram-адаптера."""

    def raise_for_status(self) -> None:
        """Подтвердить успешный HTTP-ответ."""


class FakeHttpxClient:
    """Контекстный HTTP-клиент, фиксирующий исходящие Telegram-запросы."""

    requests: list[tuple[str, dict[str, str]]] = []

    def __init__(self, *, timeout: float) -> None:
        """Принять timeout без создания сетевого подключения."""
        self.timeout = timeout

    def __enter__(self) -> "FakeHttpxClient":
        """Вернуть клиент контекстного менеджера."""
        return self

    def __exit__(self, *_: object) -> None:
        """Завершить контекст без действий."""

    def post(self, url: str, *, json: dict[str, str]) -> FakeHttpxResponse:
        """Запомнить запрос вместо реальной HTTP-отправки."""
        self.requests.append((url, json))
        return FakeHttpxResponse()


class FakeSmtpClient:
    """SMTP-клиент, фиксирующий вызовы без соединения с почтовым сервером."""

    started_tls = False
    logged_in: tuple[str, str] | None = None
    sent_subject: str | None = None

    def __init__(self, host: str, port: int) -> None:
        """Принять реквизиты SMTP без открытия соединения."""
        self.host = host
        self.port = port

    def __enter__(self) -> "FakeSmtpClient":
        """Вернуть клиент контекстного менеджера."""
        return self

    def __exit__(self, *_: object) -> None:
        """Завершить контекст без действий."""

    def starttls(self, *, context: object) -> None:
        """Зафиксировать включение STARTTLS."""
        type(self).started_tls = context is not None

    def login(self, user: str, password: str) -> None:
        """Зафиксировать аутентификацию SMTP."""
        type(self).logged_in = (user, password)

    def send_message(self, message: object) -> None:
        """Зафиксировать отправку письма."""
        type(self).sent_subject = str(message["Subject"])


class ForbiddenTelegramResponse:
    """Ответ Telegram с безопасно имитируемым отказом доступа."""

    def raise_for_status(self) -> None:
        """Сымитировать HTTP 403 без обращения к сети."""
        request = httpx.Request("POST", "https://api.telegram.org")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("Forbidden", request=request, response=response)


class ForbiddenTelegramClient(FakeHttpxClient):
    """HTTPX-клиент, возвращающий отказ Telegram вместо сетевого вызова."""

    def post(self, url: str, *, json: dict[str, str]) -> ForbiddenTelegramResponse:
        """Вернуть HTTP 403, не сохраняя URL с токеном бота."""
        return ForbiddenTelegramResponse()


class AuthenticationFailedSmtpClient(FakeSmtpClient):
    """SMTP-клиент, имитирующий ошибку учётных данных."""

    def __init__(self, host: str, port: int, *, context: object) -> None:
        """Принять SSL-контекст без открытия соединения."""
        super().__init__(host, port)
        self.context = context

    def login(self, user: str, password: str) -> None:
        """Сымитировать отказ SMTP-аутентификации."""
        raise smtplib.SMTPAuthenticationError(535, b"Authentication failed")


@pytest.fixture
def lead() -> Lead:
    """Создать заявку для прямых проверок адаптеров."""
    return Lead(
        id=7,
        name="Анна",
        contact="+79990000000",
        comment="Нужна консультация",
        source="website",
    )


@pytest.fixture
def configured_app(app: FastAPI) -> FastAPI:
    """Добавить в изолированное приложение уникальный Fernet-ключ."""
    app.state.settings = Settings(
        database_url=app.state.settings.database_url,
        api_key=app.state.settings.api_key,
        secrets_key=SecretStr(Fernet.generate_key().decode("utf-8")),
        log_directory=app.state.settings.log_directory,
    )
    return app


def test_telegram_notifier_sends_message_to_every_chat(
    lead: Lead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram-адаптер выполняет один HTTP-запрос для каждого чата."""
    FakeHttpxClient.requests = []
    monkeypatch.setattr(
        "lead_intake.services.notifications.telegram.httpx.Client",
        FakeHttpxClient,
    )
    notifier = TelegramNotifier(
        {"bot_token": "telegram-token", "chat_ids": ["100", "200"]}
    )

    result = notifier.send(lead)

    assert result.success is True
    assert len(FakeHttpxClient.requests) == 2
    assert FakeHttpxClient.requests[0][1]["chat_id"] == "100"


def test_telegram_notifier_classifies_forbidden_http_status(
    lead: Lead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram-адаптер сохраняет безопасный код отказа доступа."""
    monkeypatch.setattr(
        "lead_intake.services.notifications.telegram.httpx.Client",
        ForbiddenTelegramClient,
    )
    notifier = TelegramNotifier({"bot_token": "telegram-token", "chat_ids": ["100"]})

    result = notifier.send(lead)

    assert result.success is False
    assert result.message == "Telegram delivery failed"
    assert result.reason == "telegram_forbidden"
    assert result.provider_status == 403
    assert result.exception_type == "HTTPStatusError"


@pytest.mark.parametrize(
    ("status_code", "description", "reason"),
    [
        (400, "Bad Request: chat not found", "telegram_chat_not_found"),
        (
            403,
            "Forbidden: bot was blocked by the user",
            "telegram_bot_blocked_by_user",
        ),
        (403, "Forbidden: user is deactivated", "telegram_user_deactivated"),
        (
            400,
            "Bad Request: group chat was upgraded to a supergroup chat",
            "telegram_chat_migrated",
        ),
        (
            403,
            "Forbidden: bot is not a member of the channel chat",
            "telegram_chat_write_forbidden",
        ),
    ],
)
def test_telegram_status_error_maps_known_safe_reason_codes(
    status_code: int,
    description: str,
    reason: str,
) -> None:
    """Описание Telegram используется только для выбора безопасного кода."""
    request = httpx.Request("POST", "https://api.telegram.org")
    response = httpx.Response(
        status_code,
        json={"description": description},
        request=request,
    )
    error = httpx.HTTPStatusError("Provider error", request=request, response=response)

    result = _status_error_result(error)

    assert result.reason == reason
    assert result.provider_status == status_code
    assert result.message == "Telegram delivery failed"
    assert description not in str(result)


def test_email_notifier_uses_starttls_and_smtp_login(
    lead: Lead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Email-адаптер использует STARTTLS и не выполняет реальную отправку."""
    FakeSmtpClient.started_tls = False
    FakeSmtpClient.logged_in = None
    FakeSmtpClient.sent_subject = None
    monkeypatch.setattr(
        "lead_intake.services.notifications.email.smtplib.SMTP",
        FakeSmtpClient,
    )
    notifier = EmailNotifier(
        {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "tls_mode": "starttls",
            "smtp_user": "user@example.com",
            "smtp_password": "smtp-password",
            "from_address": "from@example.com",
            "recipients": ["to@example.com"],
            "subject_template": "Заявка #{id}",
        }
    )

    result = notifier.send(lead)

    assert result.success is True
    assert FakeSmtpClient.started_tls is True
    assert FakeSmtpClient.logged_in == ("user@example.com", "smtp-password")
    assert FakeSmtpClient.sent_subject == "Заявка #7"


def test_email_notifier_classifies_authentication_failure(
    lead: Lead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Email-адаптер сохраняет безопасный код ошибки SMTP-аутентификации."""
    monkeypatch.setattr(
        "lead_intake.services.notifications.email.smtplib.SMTP_SSL",
        AuthenticationFailedSmtpClient,
    )
    notifier = EmailNotifier(
        {
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "tls_mode": "ssl",
            "smtp_user": "user@example.com",
            "smtp_password": "smtp-password",
            "from_address": "from@example.com",
            "recipients": ["to@example.com"],
        }
    )

    result = notifier.send(lead)

    assert result.success is False
    assert result.message == "Email delivery failed"
    assert result.reason == "smtp_auth_failed"
    assert result.provider_status == 535
    assert result.exception_type == "SMTPAuthenticationError"


def test_dispatcher_isolates_channel_failures(
    configured_app: FastAPI,
) -> None:
    """Сбой Telegram не мешает Email и фиксируется отдельными событиями."""
    lead = _create_lead(configured_app)
    _add_channel(
        configured_app,
        NotificationChannelType.TELEGRAM,
        {"bot_token": "telegram-token", "chat_ids": ["100"]},
    )
    _add_channel(
        configured_app,
        NotificationChannelType.EMAIL,
        {
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "tls_mode": "ssl",
            "from_address": "from@example.com",
            "recipients": ["to@example.com"],
        },
    )

    with (
        patch(
            "lead_intake.services.notifications.dispatcher.TelegramNotifier.send",
            return_value=NotificationResult(False, "Telegram delivery failed"),
        ),
        patch(
            "lead_intake.services.notifications.dispatcher.EmailNotifier.send",
            return_value=NotificationResult(True),
        ),
    ):
        dispatch_notifications(
            configured_app.state.session_factory,
            configured_app.state.settings,
            lead.id,
        )

    events = _notification_events(configured_app)

    assert [event.type for event in events] == [
        "notification_failed",
        "notification_sent",
    ]


def test_lead_is_saved_when_all_notifications_fail(
    configured_app: FastAPI,
    client: TestClient,
) -> None:
    """Падение всех каналов не отменяет уже сохранённую заявку."""
    _add_channel(
        configured_app,
        NotificationChannelType.TELEGRAM,
        {"bot_token": "telegram-token", "chat_ids": ["100"]},
    )

    with patch(
        "lead_intake.services.notifications.dispatcher.TelegramNotifier.send",
        return_value=NotificationResult(False, "Telegram delivery failed"),
    ):
        response = client.post(
            "/api/v1/leads",
            headers=API_HEADERS,
            json={"contact": "+79990000001", "source": "website"},
        )

    session = configured_app.state.session_factory()
    try:
        saved_lead = session.scalar(select(Lead).where(Lead.contact == "+79990000001"))
        failure_event = session.scalar(
            select(Event).where(Event.type == "notification_failed")
        )
    finally:
        session.close()

    assert response.status_code == 201
    assert saved_lead is not None
    assert failure_event is not None


def test_failed_delivery_logs_safe_diagnostic_fields(
    configured_app: FastAPI,
) -> None:
    """Диспетчер передаёт в лог код причины и attempt_id без секретов."""
    lead = _create_lead(configured_app)
    secret_token = "telegram-token-must-not-be-logged"
    _add_channel(
        configured_app,
        NotificationChannelType.TELEGRAM,
        {"bot_token": secret_token, "chat_ids": ["100"]},
    )

    with (
        patch(
            "lead_intake.services.notifications.dispatcher.TelegramNotifier.send",
            return_value=NotificationResult(
                success=False,
                message="Telegram delivery failed",
                reason="telegram_forbidden",
                provider_status=403,
                exception_type="HTTPStatusError",
            ),
        ),
        patch(
            "lead_intake.services.notifications.dispatcher.logger.warning"
        ) as log_warning,
    ):
        dispatch_notifications(
            configured_app.state.session_factory,
            configured_app.state.settings,
            lead.id,
        )

    assert log_warning.call_args.args == ("Notification delivery failed",)
    diagnostic = log_warning.call_args.kwargs["extra"]
    assert diagnostic["lead_id"] == lead.id
    assert diagnostic["channel_id"] == 1
    assert diagnostic["channel_type"] == "telegram"
    assert diagnostic["reason"] == "telegram_forbidden"
    assert diagnostic["provider_status"] == 403
    assert diagnostic["exception_type"] == "HTTPStatusError"
    assert len(diagnostic["attempt_id"]) == 32
    assert secret_token not in str(diagnostic)


def _create_lead(app: FastAPI) -> Lead:
    """Сохранить заявку напрямую для проверки фонового диспетчера."""
    session = app.state.session_factory()
    try:
        lead = Lead(contact="+79990000000", comment="", source="website")
        session.add(lead)
        session.commit()
        return lead
    finally:
        session.close()


def _add_channel(
    app: FastAPI,
    channel_type: NotificationChannelType,
    config: dict[str, object],
) -> None:
    """Добавить включённый канал с зашифрованной конфигурацией в БД."""
    secrets_key = app.state.settings.secrets_key
    assert secrets_key is not None
    encrypted_config = encrypt_channel_config(
        channel_type.value,
        config,
        create_fernet(secrets_key.get_secret_value()),
    )
    session = app.state.session_factory()
    try:
        session.add(NotificationChannel(type=channel_type, config=encrypted_config))
        session.commit()
    finally:
        session.close()


def _notification_events(app: FastAPI) -> list[Event]:
    """Вернуть события доставки в порядке их создания."""
    session = app.state.session_factory()
    try:
        return list(
            session.scalars(
                select(Event)
                .where(Event.type.in_(["notification_sent", "notification_failed"]))
                .order_by(Event.id)
            )
        )
    finally:
        session.close()
