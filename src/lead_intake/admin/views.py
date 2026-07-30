"""HTML-админка заявок и настроек уведомлений."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_intake.config import Settings
from lead_intake.db import get_session
from lead_intake.models import (
    Event,
    LeadStatus,
    NotificationChannel,
    NotificationChannelType,
)
from lead_intake.security import (
    decrypt_channel_config,
    encrypt_secret,
    get_fernet,
    require_admin,
)
from lead_intake.services.leads import get_leads, update_lead_status
from lead_intake.services.notifications.dispatcher import send_test_notification

templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))
router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)


@router.get("/leads", response_class=HTMLResponse)
def show_leads(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    source: Annotated[str | None, Query(max_length=100)] = None,
    status_value: Annotated[str | None, Query(alias="status")] = None,
) -> HTMLResponse:
    """Показать таблицу заявок с фильтрами источника и статуса."""
    try:
        lead_status = LeadStatus(status_value) if status_value else None
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT
        ) from error
    lead_page = get_leads(session, 1, 100, source, lead_status)
    return templates.TemplateResponse(
        request=request,
        name="leads.html",
        context={
            "leads": lead_page.items,
            "source": source or "",
            "selected_status": lead_status,
            "statuses": list(LeadStatus),
        },
    )


@router.post("/leads/{lead_id}/status", response_class=HTMLResponse)
async def change_lead_status(
    lead_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Сменить статус заявки и вернуть HTMX-фрагмент статуса."""
    fields = await _read_urlencoded_fields(request)
    try:
        new_status = LeadStatus(fields.get("status"))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT
        ) from error

    lead = update_lead_status(session, lead_id, new_status)
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )
    return templates.TemplateResponse(
        request=request,
        name="partials/status_badge.html",
        context={"lead": lead},
    )


@router.get("/leads/{lead_id}/events", response_class=HTMLResponse)
def show_lead_events(
    lead_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Показать журнал событий выбранной заявки."""
    events = list(
        session.scalars(
            select(Event)
            .where(Event.lead_id == lead_id)
            .order_by(Event.created_at.desc())
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="events.html",
        context={"lead_id": lead_id, "events": events},
    )


@router.get("/settings", response_class=HTMLResponse)
def show_notification_settings(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Показать формы настройки Telegram и Email-уведомлений."""
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=_settings_context(session, request),
    )


@router.post("/settings/telegram", response_class=HTMLResponse)
async def save_telegram_settings(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Сохранить настройки Telegram с шифрованием токена бота."""
    fields = await _read_urlencoded_fields(request)
    channel = _find_channel(session, NotificationChannelType.TELEGRAM)
    try:
        config = _build_telegram_config(fields, channel, request)
    except ValueError:
        return _result_fragment(
            request,
            "Заполните обязательные настройки Telegram.",
            success=False,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    channel, can_enable = _save_channel(
        session,
        channel,
        NotificationChannelType.TELEGRAM,
        config,
        enabled=None,
        verified=False,
        settings=request.app.state.settings,
    )
    message = (
        "Настройки Telegram сохранены. Выполните проверочную отправку."
        if not can_enable
        else "Настройки Telegram сохранены."
    )
    return _result_fragment(request, message, success=True)


@router.post("/settings/email", response_class=HTMLResponse)
async def save_email_settings(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Сохранить настройки Email с шифрованием SMTP-пароля."""
    fields = await _read_urlencoded_fields(request)
    channel = _find_channel(session, NotificationChannelType.EMAIL)
    try:
        config = _build_email_config(fields, channel, request)
    except ValueError:
        return _result_fragment(
            request,
            "Заполните обязательные настройки Email.",
            success=False,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    channel, can_enable = _save_channel(
        session,
        channel,
        NotificationChannelType.EMAIL,
        config,
        enabled=None,
        verified=False,
        settings=request.app.state.settings,
    )
    message = (
        "Настройки Email сохранены. Выполните проверочную отправку."
        if not can_enable
        else "Настройки Email сохранены."
    )
    return _result_fragment(request, message, success=True)


@router.post("/settings/{channel_type}/enabled", response_class=HTMLResponse)
async def change_channel_enabled(
    channel_type: NotificationChannelType,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Сохранить переключение канала без сохранения остальных полей формы."""
    fields = await _read_urlencoded_fields(request)
    channel = _find_channel(session, channel_type)
    requested_enabled = fields.get("enabled") == "on"
    if channel is None:
        return _result_fragment(
            request,
            "Сначала сохраните настройки и выполните проверочную отправку.",
            success=False,
            status_code=status.HTTP_409_CONFLICT,
        )
    if requested_enabled and not _is_config_verified(channel):
        channel.enabled = False
        session.commit()
        return _verification_required_result(request, channel)

    channel.enabled = requested_enabled
    session.commit()
    message = "Канал включён." if requested_enabled else "Канал выключен."
    return _result_fragment(
        request,
        message,
        success=True,
        headers=_channel_enabled_headers(channel),
    )


@router.post("/settings/{channel_type}/test", response_class=HTMLResponse)
async def test_channel_settings(
    channel_type: NotificationChannelType,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Проверить введённые настройки через отдельного проверочного получателя."""
    fields = await _read_urlencoded_fields(request)
    channel = _find_channel(session, channel_type)
    try:
        config = _build_channel_config(channel_type, fields, channel, request)
        test_config = _build_test_config(channel_type, config)
    except ValueError:
        return _result_fragment(
            request,
            "Заполните настройки и проверочных получателей.",
            success=False,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    result = send_test_notification(
        request.app.state.settings,
        channel_type,
        test_config,
    )
    channel, _ = _save_channel(
        session,
        channel,
        channel_type,
        config,
        enabled=None,
        verified=result.success,
        settings=request.app.state.settings,
    )
    message = (
        "Проверочная отправка выполнена."
        if result.success
        else ("Проверочная отправка не выполнена.")
    )
    return _result_fragment(
        request,
        message,
        success=result.success,
        headers=_verification_headers(channel_type, _verification_context(channel)),
    )


async def _read_urlencoded_fields(request: Request) -> dict[str, str]:
    """Прочитать URL-encoded форму без дополнительной зависимости multipart."""
    fields = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] for key, values in fields.items()}


def _settings_context(session: Session, request: Request) -> dict[str, Any]:
    """Сформировать контекст шаблона с маскированными секретами каналов."""
    return {
        "telegram": _display_channel(
            _find_channel(session, NotificationChannelType.TELEGRAM),
            NotificationChannelType.TELEGRAM,
            request,
        ),
        "email": _display_channel(
            _find_channel(session, NotificationChannelType.EMAIL),
            NotificationChannelType.EMAIL,
            request,
        ),
    }


def _display_channel(
    channel: NotificationChannel | None,
    channel_type: NotificationChannelType,
    request: Request,
) -> dict[str, Any]:
    """Подготовить конфигурацию канала для UI, не раскрывая секреты."""
    if channel is None:
        return {
            "enabled": False,
            "config": {},
            "verification": _verification_context(None),
        }

    display_config = dict(channel.config)
    try:
        decrypted_config = decrypt_channel_config(
            channel_type.value,
            channel.config,
            get_fernet(request.app.state.settings.secrets_key),
        )
    except ValueError:
        decrypted_config = {}

    for field_name in {"bot_token", "smtp_password"}:
        value = decrypted_config.get(field_name)
        if isinstance(value, str):
            display_config[field_name] = (
                "Секрет сохранён"
                if field_name == "smtp_password"
                else _mask_secret(value)
            )
        else:
            display_config.pop(field_name, None)
    return {
        "enabled": channel.enabled,
        "config": display_config,
        "verification": _verification_context(channel),
    }


def _mask_secret(value: str) -> str:
    """Вернуть маску секрета с последними четырьмя символами при их наличии."""
    return f"••••{value[-4:]}" if len(value) > 4 else "••••"


def _find_channel(
    session: Session,
    channel_type: NotificationChannelType,
) -> NotificationChannel | None:
    """Найти сохранённый канал уведомлений по его типу."""
    return session.scalar(
        select(NotificationChannel).where(NotificationChannel.type == channel_type)
    )


def _build_telegram_config(
    fields: dict[str, str],
    channel: NotificationChannel | None,
    request: Request,
) -> dict[str, Any]:
    """Собрать Telegram-конфигурацию, сохраняя прежний токен при пустом поле."""
    chat_ids = _split_values(fields.get("chat_ids", ""))
    if not chat_ids:
        raise ValueError("Telegram chat_ids are required")

    config: dict[str, Any] = {
        "chat_ids": chat_ids,
        "test_chat_ids": _split_values(fields.get("test_chat_ids", "")),
    }
    _preserve_or_encrypt_secret("bot_token", fields, channel, config, request)
    if "bot_token" not in config:
        raise ValueError("Telegram bot_token is required")
    return config


def _build_email_config(
    fields: dict[str, str],
    channel: NotificationChannel | None,
    request: Request,
) -> dict[str, Any]:
    """Собрать Email-конфигурацию, сохраняя прежний SMTP-пароль при пустом поле."""
    try:
        smtp_port = int(fields.get("smtp_port", ""))
    except ValueError as error:
        raise ValueError("SMTP port is required") from error
    tls_mode = fields.get("tls_mode", "")
    recipients = _split_values(fields.get("recipients", ""))
    required_fields = ["smtp_host", "from_address"]
    if (
        smtp_port <= 0
        or tls_mode not in {"ssl", "starttls"}
        or not recipients
        or any(not fields.get(field_name, "").strip() for field_name in required_fields)
    ):
        raise ValueError("Email configuration is incomplete")

    config: dict[str, Any] = {
        "smtp_host": fields["smtp_host"].strip(),
        "smtp_port": smtp_port,
        "tls_mode": tls_mode,
        "smtp_user": fields.get("smtp_user", "").strip(),
        "from_address": fields["from_address"].strip(),
        "recipients": recipients,
        "test_recipients": _split_values(fields.get("test_recipients", "")),
        "subject_template": fields.get("subject_template", "Новая заявка #{id}").strip()
        or "Новая заявка #{id}",
    }
    _preserve_or_encrypt_secret("smtp_password", fields, channel, config, request)
    return config


def _build_channel_config(
    channel_type: NotificationChannelType,
    fields: dict[str, str],
    channel: NotificationChannel | None,
    request: Request,
) -> dict[str, Any]:
    """Собрать конфигурацию нужного канала из введённых значений."""
    if channel_type is NotificationChannelType.TELEGRAM:
        return _build_telegram_config(fields, channel, request)
    if channel_type is NotificationChannelType.EMAIL:
        return _build_email_config(fields, channel, request)
    raise ValueError("Unsupported notification channel type")


def _build_test_config(
    channel_type: NotificationChannelType,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Заменить рабочих получателей отдельным списком для проверки."""
    test_config = dict(config)
    if channel_type is NotificationChannelType.TELEGRAM:
        test_chat_ids = config.get("test_chat_ids")
        if not isinstance(test_chat_ids, list) or not test_chat_ids:
            raise ValueError("Telegram test_chat_ids are required")
        test_config["chat_ids"] = test_chat_ids
        return test_config

    test_recipients = config.get("test_recipients")
    if not isinstance(test_recipients, list) or not test_recipients:
        raise ValueError("Email test_recipients are required")
    test_config["recipients"] = test_recipients
    return test_config


def _preserve_or_encrypt_secret(
    field_name: str,
    fields: dict[str, str],
    channel: NotificationChannel | None,
    config: dict[str, Any],
    request: Request,
) -> None:
    """Зашифровать новый секрет или сохранить уже зашифрованное значение."""
    value = fields.get(field_name, "")
    if value:
        config[field_name] = encrypt_secret(
            value,
            get_fernet(request.app.state.settings.secrets_key),
        )
    elif channel is not None and field_name in channel.config:
        config[field_name] = channel.config[field_name]


def _split_values(value: str) -> list[str]:
    """Разобрать список значений, разделённых запятыми или переводами строк."""
    return [
        item.strip() for item in value.replace("\n", ",").split(",") if item.strip()
    ]


def _save_channel(
    session: Session,
    channel: NotificationChannel | None,
    channel_type: NotificationChannelType,
    config: dict[str, Any],
    *,
    enabled: bool | None,
    verified: bool,
    settings: Settings,
) -> tuple[NotificationChannel, bool]:
    """Сохранить канал и обновить состояние проверки его конфигурации."""
    if channel is None:
        channel = NotificationChannel(
            type=channel_type,
            enabled=False,
            config_revision=1,
        )
        session.add(channel)
        config_changed = True
    else:
        config_changed = _important_settings_changed(
            channel,
            channel_type,
            config,
            settings,
        )
        if config_changed:
            channel.config_revision += 1

    channel.config = config
    if config_changed:
        channel.enabled = False

    if verified:
        channel.verified_config_revision = channel.config_revision
        channel.verified_at = datetime.now(UTC)

    can_enable = _is_config_verified(channel)
    if enabled is not None:
        channel.enabled = enabled if can_enable else False

    session.commit()
    return channel, can_enable


def _important_settings_changed(
    channel: NotificationChannel,
    channel_type: NotificationChannelType,
    config: dict[str, Any],
    settings: Settings,
) -> bool:
    """Определить изменение параметров, влияющих на доставку уведомлений."""
    fields_by_channel = {
        NotificationChannelType.TELEGRAM: ("bot_token",),
        NotificationChannelType.EMAIL: (
            "smtp_host",
            "smtp_port",
            "tls_mode",
            "smtp_user",
            "smtp_password",
            "from_address",
        ),
    }
    try:
        fernet = get_fernet(settings.secrets_key)
        current_config = decrypt_channel_config(
            channel_type.value,
            channel.config,
            fernet,
        )
        new_config = decrypt_channel_config(channel_type.value, config, fernet)
    except ValueError:
        return True

    return any(
        current_config.get(field_name) != new_config.get(field_name)
        for field_name in fields_by_channel[channel_type]
    )


def _is_config_verified(channel: NotificationChannel) -> bool:
    """Проверить, относится ли успешная проверка к текущей версии настроек."""
    return channel.verified_config_revision == channel.config_revision


def _verification_context(channel: NotificationChannel | None) -> dict[str, Any]:
    """Подготовить безопасный статус проверки конфигурации для интерфейса."""
    if channel is None:
        return {
            "status": "not_configured",
            "message": "Настройте канал и выполните проверочную отправку.",
            "can_enable": False,
        }
    if _is_config_verified(channel):
        verified_at = channel.verified_at
        timestamp = (
            verified_at.strftime("%d.%m.%Y %H:%M")
            if verified_at is not None
            else "только что"
        )
        return {
            "status": "verified",
            "message": f"Проверено: {timestamp}",
            "can_enable": True,
        }
    return {
        "status": "required",
        "message": "Требуется проверочная отправка.",
        "can_enable": False,
    }


def _verification_required_result(
    request: Request,
    channel: NotificationChannel,
) -> HTMLResponse:
    """Вернуть понятный результат попытки включить непроверенный канал."""
    return _result_fragment(
        request,
        "Канал можно включить после успешной проверочной отправки.",
        success=False,
        status_code=status.HTTP_409_CONFLICT,
        headers=_verification_headers(channel.type, _verification_context(channel)),
    )


def _verification_headers(
    channel_type: NotificationChannelType,
    verification: dict[str, Any],
) -> dict[str, str]:
    """Передать клиентскому коду новое состояние проверки через HTMX."""
    return {
        "HX-Trigger": json.dumps(
            {
                "channelVerificationUpdated": {
                    "channelType": channel_type.value,
                    "verified": verification["can_enable"],
                    "status": verification["status"],
                    "message": verification["message"],
                }
            }
        )
    }


def _channel_enabled_headers(channel: NotificationChannel) -> dict[str, str]:
    """Передать клиентскому коду фактически сохранённое состояние канала."""
    return {
        "HX-Trigger": json.dumps(
            {
                "channelEnabledUpdated": {
                    "channelType": channel.type.value,
                    "enabled": channel.enabled,
                }
            }
        )
    }


def _result_fragment(
    request: Request,
    message: str,
    success: bool,
    *,
    status_code: int = status.HTTP_200_OK,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """Вернуть HTMX-фрагмент безопасного результата действия в админке."""
    return templates.TemplateResponse(
        request=request,
        name="partials/settings_result.html",
        context={"message": message, "success": success},
        status_code=status_code,
        headers=headers,
    )
