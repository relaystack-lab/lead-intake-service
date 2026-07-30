"""Проверки безопасного файлового JSONL-логирования."""

import gzip
import json
import logging
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lead_intake.logging_config import (
    GzipTimedRotatingFileHandler,
    JsonFormatter,
    configure_logging,
)

API_HEADERS = {"X-API-Key": "test-api-key"}


def test_validation_error_log_contains_reason_without_request_values(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Лог 422 сохраняет только категорию и имя поля без данных заявки."""
    private_comment = "конфиденциальный комментарий клиента"

    response = client.post(
        "/api/v1/leads",
        headers=API_HEADERS,
        json={"contact": "   ", "comment": private_comment},
    )
    records = _read_log_records(app)

    assert response.status_code == 422
    assert response.headers["x-request-id"]
    assert private_comment not in _log_text(app)
    assert records[-1]["event"] == "request_rejected"
    assert records[-1]["category"] == "validation_rejected"
    assert records[-1]["reason"] == "contact_empty"
    assert records[-1]["field"] == "contact"


def test_internal_error_log_does_not_contain_exception_message(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Лог 500 сохраняет тип ошибки, но не переданный в ней секрет."""
    private_value = "smtp-password-must-not-be-logged"
    with patch(
        "lead_intake.api.leads.create_or_get_lead",
        side_effect=RuntimeError(private_value),
    ):
        response = client.post(
            "/api/v1/leads",
            headers=API_HEADERS,
            json={"contact": "+79990000000"},
        )
    records = _read_log_records(app)

    assert response.status_code == 500
    assert private_value not in _log_text(app)
    assert records[-1]["event"] == "server_error"
    assert records[-1]["exception_type"] == "RuntimeError"


def test_daily_log_rotation_creates_gzip_archive(tmp_path: Path) -> None:
    """Ротация создаёт gzip-архив JSONL и использует заданный retention."""
    logger = configure_logging(str(tmp_path), retention_days=7, log_level="INFO")
    logger.info("Rotation test", extra={"event": "rotation_test"})
    file_handler = next(
        handler
        for handler in logger.handlers
        if isinstance(handler, GzipTimedRotatingFileHandler)
    )

    file_handler.doRollover()
    archives = list(tmp_path.glob("application.jsonl.*.gz"))

    assert file_handler.backupCount == 7
    assert len(archives) == 1
    with gzip.open(archives[0], "rt", encoding="utf-8") as archive:
        assert json.loads(archive.readline())["event"] == "rotation_test"


def test_json_formatter_keeps_safe_delivery_diagnostic_fields() -> None:
    """Форматтер пишет поля диагностики доставки и отбрасывает секреты."""
    record = logging.LogRecord(
        name="lead_intake",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Notification delivery failed",
        args=(),
        exc_info=None,
    )
    record.event = "notification_delivery_failed"
    record.category = "notification_delivery"
    record.lead_id = 42
    record.channel_id = 7
    record.channel_type = "telegram"
    record.attempt_id = "cafe" * 8
    record.reason = "telegram_forbidden"
    record.provider_status = 403
    record.exception_type = "HTTPStatusError"
    record.bot_token = "must-not-be-logged"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["lead_id"] == 42
    assert payload["channel_id"] == 7
    assert payload["channel_type"] == "telegram"
    assert payload["attempt_id"] == "cafe" * 8
    assert payload["reason"] == "telegram_forbidden"
    assert payload["provider_status"] == 403
    assert payload["exception_type"] == "HTTPStatusError"
    assert "bot_token" not in payload


def _read_log_records(app: FastAPI) -> list[dict[str, object]]:
    """Прочитать JSONL-записи текущего изолированного приложения."""
    log_path = Path(app.state.settings.log_directory) / "application.jsonl"
    return [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]


def _log_text(app: FastAPI) -> str:
    """Вернуть всё содержимое текущего JSONL-файла для проверки утечек."""
    log_path = Path(app.state.settings.log_directory) / "application.jsonl"
    return log_path.read_text(encoding="utf-8")
