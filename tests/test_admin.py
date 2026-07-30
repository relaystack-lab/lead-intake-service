"""Интеграционные проверки HTML-админки."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from lead_intake.models import (
    Event,
    Lead,
    LeadStatus,
    NotificationChannel,
    NotificationChannelType,
)
from lead_intake.services.notifications.base import NotificationResult

ADMIN_AUTH = ("admin", "admin-password")
API_HEADERS = {"X-API-Key": "test-api-key"}


def test_admin_requires_http_basic_authentication(client: TestClient) -> None:
    """Административные страницы без учётных данных возвращают 401."""
    response = client.get("/admin/leads")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="Lead Intake Admin"'


def test_admin_pages_reference_served_stylesheet(client: TestClient) -> None:
    """Страницы админки подключают общие статические ресурсы."""
    page_response = client.get("/admin/leads", auth=ADMIN_AUTH)
    stylesheet_response = client.get("/static/admin.css")
    script_response = client.get("/static/admin.js")

    assert page_response.status_code == 200
    assert "/static/admin.css" in page_response.text
    assert "/static/admin.js" in page_response.text
    assert stylesheet_response.status_code == 200
    assert ".toggle-control" in stylesheet_response.text
    assert script_response.status_code == 200
    assert "channelVerificationUpdated" in script_response.text


def test_admin_changes_status_with_htmx_fragment_and_records_event(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Смена статуса возвращает фрагмент и создаёт запись в event log."""
    create_response = client.post(
        "/api/v1/leads",
        headers=API_HEADERS,
        json={"contact": "+79990000010", "source": "website"},
    )
    lead_id = create_response.json()["id"]

    response = client.post(
        f"/admin/leads/{lead_id}/status",
        auth=ADMIN_AUTH,
        data={"status": "done"},
    )
    events_response = client.get(f"/admin/leads/{lead_id}/events", auth=ADMIN_AUTH)

    session = app.state.session_factory()
    try:
        lead = session.get(Lead, lead_id)
        status_event = session.scalar(
            select(Event).where(Event.type == "lead_status_changed")
        )
    finally:
        session.close()

    assert response.status_code == 200
    assert f'id="lead-{lead_id}-status"' in response.text
    assert "Завершена" in response.text
    assert lead is not None
    assert lead.status is LeadStatus.DONE
    assert status_event is not None
    assert status_event.payload == {"from": "new", "to": "done"}
    assert events_response.status_code == 200
    assert "lead_status_changed" in events_response.text


def test_admin_lead_filter_ignores_empty_source_when_status_is_selected(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Пустой источник не исключает заявки при фильтрации по статусу."""
    response = client.post(
        "/api/v1/leads",
        headers=API_HEADERS,
        json={"contact": "+79990000011", "source": "website"},
    )
    lead_id = response.json()["id"]
    session = app.state.session_factory()
    try:
        lead = session.get(Lead, lead_id)
        assert lead is not None
        lead.status = LeadStatus.DONE
        session.commit()
    finally:
        session.close()

    filtered_response = client.get(
        "/admin/leads?source=&status=done",
        auth=ADMIN_AUTH,
    )
    source_response = client.get(
        "/admin/leads?source=web&status=",
        auth=ADMIN_AUTH,
    )

    assert filtered_response.status_code == 200
    assert "+79990000011" in filtered_response.text
    assert source_response.status_code == 200
    assert "+79990000011" in source_response.text


def test_admin_saves_encrypted_channel_secrets_and_masks_them(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Формы Telegram и Email сохраняют секреты в зашифрованном виде."""
    telegram_token = "telegram-bot-token"
    smtp_password = "smtp-password"
    telegram_response = client.post(
        "/admin/settings/telegram",
        auth=ADMIN_AUTH,
        data={
            "bot_token": telegram_token,
            "chat_ids": "100, 200",
            "test_chat_ids": "300",
        },
    )
    email_response = client.post(
        "/admin/settings/email",
        auth=ADMIN_AUTH,
        data={
            "smtp_host": "smtp.example.com",
            "smtp_port": "587",
            "tls_mode": "starttls",
            "smtp_user": "user@example.com",
            "smtp_password": smtp_password,
            "from_address": "from@example.com",
            "recipients": "to@example.com",
            "test_recipients": "test@example.com",
            "subject_template": "Заявка #{id}",
        },
    )
    settings_response = client.get("/admin/settings", auth=ADMIN_AUTH)

    session = app.state.session_factory()
    try:
        channels = {
            channel.type: channel
            for channel in session.scalars(select(NotificationChannel))
        }
    finally:
        session.close()

    assert telegram_response.status_code == 200
    assert email_response.status_code == 200
    assert (
        telegram_token
        not in channels[NotificationChannelType.TELEGRAM].config["bot_token"]
    )
    assert (
        smtp_password
        not in channels[NotificationChannelType.EMAIL].config["smtp_password"]
    )
    assert settings_response.status_code == 200
    assert telegram_token not in settings_response.text
    assert smtp_password not in settings_response.text
    assert "••••oken" in settings_response.text
    assert "Секрет сохранён" in settings_response.text
    assert 'placeholder="••••word"' not in settings_response.text


def test_successful_test_saves_current_configuration_and_unlocks_channel(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Успешный тест сохраняет конфигурацию и разрешает включение канала."""
    fields = {
        "smtp_host": "smtp.example.com",
        "smtp_port": "465",
        "tls_mode": "ssl",
        "from_address": "from@example.com",
        "recipients": "lead-recipient@example.com",
        "test_recipients": "test-recipient@example.com",
        "subject_template": "Заявка #{id}",
    }

    with patch(
        "lead_intake.services.notifications.dispatcher.EmailNotifier.send",
        return_value=NotificationResult(success=True),
    ):
        response = client.post(
            "/admin/settings/email/test",
            auth=ADMIN_AUTH,
            data=fields,
        )

    session = app.state.session_factory()
    try:
        channel = session.scalar(
            select(NotificationChannel).where(
                NotificationChannel.type == NotificationChannelType.EMAIL
            )
        )
    finally:
        session.close()

    assert response.status_code == 200
    assert "Проверочная отправка выполнена." in response.text
    assert channel is not None
    assert channel.config["recipients"] == ["lead-recipient@example.com"]
    assert channel.config["test_recipients"] == ["test-recipient@example.com"]
    assert channel.enabled is False
    assert channel.verified_config_revision == channel.config_revision
    assert channel.verified_at is not None
    assert "channelVerificationUpdated" in response.headers["hx-trigger"]

    enable_response = client.post(
        "/admin/settings/email/enabled",
        auth=ADMIN_AUTH,
        data={"enabled": "on"},
    )
    session = app.state.session_factory()
    try:
        enabled_channel = session.get(NotificationChannel, channel.id)
    finally:
        session.close()

    assert enable_response.status_code == 200
    assert enabled_channel is not None
    assert enabled_channel.enabled is True


def test_important_settings_change_requires_new_successful_test(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Смена SMTP-хоста отключает канал до следующего успешного теста."""
    fields = {
        "smtp_host": "smtp.example.com",
        "smtp_port": "465",
        "tls_mode": "ssl",
        "from_address": "from@example.com",
        "recipients": "lead-recipient@example.com",
        "test_recipients": "test-recipient@example.com",
        "subject_template": "Заявка #{id}",
    }
    with patch(
        "lead_intake.services.notifications.dispatcher.EmailNotifier.send",
        return_value=NotificationResult(success=True),
    ):
        client.post("/admin/settings/email/test", auth=ADMIN_AUTH, data=fields)
    client.post(
        "/admin/settings/email/enabled",
        auth=ADMIN_AUTH,
        data={"enabled": "on"},
    )

    session = app.state.session_factory()
    try:
        configured_channel = session.scalar(select(NotificationChannel))
        previous_revision = configured_channel.config_revision
    finally:
        session.close()

    response = client.post(
        "/admin/settings/email",
        auth=ADMIN_AUTH,
        data={**fields, "smtp_host": "new-smtp.example.com"},
    )

    session = app.state.session_factory()
    try:
        changed_channel = session.scalar(select(NotificationChannel))
    finally:
        session.close()

    assert response.status_code == 200
    assert "Выполните проверочную отправку." in response.text
    assert changed_channel is not None
    assert changed_channel.enabled is False
    assert changed_channel.config_revision == previous_revision + 1
    assert changed_channel.verified_config_revision != changed_channel.config_revision


def test_test_requires_separate_test_recipients(client: TestClient) -> None:
    """Проверочная отправка не использует рабочих получателей как резервных."""
    response = client.post(
        "/admin/settings/email/test",
        auth=ADMIN_AUTH,
        data={
            "smtp_host": "smtp.example.com",
            "smtp_port": "465",
            "tls_mode": "ssl",
            "from_address": "from@example.com",
            "recipients": "lead-recipient@example.com",
        },
    )

    assert response.status_code == 422
    assert "проверочных получателей" in response.text
