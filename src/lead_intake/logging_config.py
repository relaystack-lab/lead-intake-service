"""Настройка структурированного JSONL-логирования приложения."""

import gzip
import json
import logging
import shutil
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import Request

SAFE_LOG_FIELDS = (
    "event",
    "category",
    "reason",
    "request_id",
    "path",
    "client_ip",
    "status_code",
    "field",
    "exception_type",
    "lead_id",
    "channel_id",
    "channel_type",
    "attempt_id",
    "provider_status",
)


class JsonFormatter(logging.Formatter):
    """Форматировать записи в JSON без тел запросов, ключей и секретов."""

    def format(self, record: logging.LogRecord) -> str:
        """Вернуть безопасное структурированное представление записи лога."""
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in SAFE_LOG_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
        return json.dumps(payload, ensure_ascii=False)


class GzipTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Ежедневно архивировать JSONL-лог в gzip и удалять старые архивы."""

    def __init__(self, filename: Path, retention_days: int) -> None:
        """Настроить ежедневную ротацию в UTC с заданным числом архивов."""
        super().__init__(
            filename,
            when="midnight",
            interval=1,
            backupCount=retention_days,
            encoding="utf-8",
            delay=True,
            utc=True,
        )
        self.namer = self._gzip_name
        self.rotator = self._gzip_rotate

    @staticmethod
    def _gzip_name(default_name: str) -> str:
        """Добавить расширение gzip к имени ежедневного архива."""
        return f"{default_name}.gz"

    @staticmethod
    def _gzip_rotate(source: str, destination: str) -> None:
        """Сжать завершённый JSONL-файл и удалить исходную версию."""
        with (
            Path(source).open("rb") as source_file,
            gzip.open(destination, "wb") as archive,
        ):
            shutil.copyfileobj(source_file, archive)
        Path(source).unlink()


def configure_logging(
    log_directory: str,
    retention_days: int,
    log_level: str,
) -> logging.Logger:
    """Настроить stdout и ежедневный архивируемый JSONL-файл приложения."""
    directory = Path(log_directory)
    directory.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("lead_intake")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    formatter = JsonFormatter()
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = GzipTimedRotatingFileHandler(
        directory / "application.jsonl",
        retention_days,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    logger.setLevel(log_level.upper())
    logger.propagate = False
    return logger


def request_log_extra(
    request: Request,
    **extra_fields: str | int,
) -> dict[str, str | int]:
    """Сформировать безопасные поля лога для одного HTTP-запроса."""
    extra: dict[str, str | int] = {
        "request_id": getattr(request.state, "request_id", "unknown"),
        "path": request.url.path,
    }
    if request.client is not None:
        extra["client_ip"] = request.client.host
    extra.update(extra_fields)
    return extra
