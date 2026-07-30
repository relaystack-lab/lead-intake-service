"""Общие фикстуры проверок."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from lead_intake.config import Settings
from lead_intake.main import create_app
from lead_intake.models import Base


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """Создать приложение с изолированной SQLite-БД."""
    database_path = tmp_path / "lead_intake.db"
    settings = Settings(
        database_url=f"sqlite:///{database_path.as_posix()}",
        api_key=SecretStr("test-api-key"),
        admin_username="admin",
        admin_password=SecretStr("admin-password"),
        secrets_key=SecretStr(Fernet.generate_key().decode("utf-8")),
        log_directory=str(tmp_path / "logs"),
    )
    application = create_app(settings)
    Base.metadata.create_all(application.state.engine)
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Предоставить HTTP-клиент с выполненным lifespan приложения."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
