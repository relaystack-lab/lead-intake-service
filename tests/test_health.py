"""Проверки endpoint состояния сервиса."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError


def test_health_returns_ok_when_database_is_available(client: TestClient) -> None:
    """Endpoint состояния подтверждает доступность SQLite-БД."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_unavailable_when_database_connection_fails(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Endpoint состояния не раскрывает детали недоступной БД."""
    error = OperationalError("SELECT 1", {}, Exception("database is unavailable"))

    with patch.object(app.state.engine, "connect", side_effect=error):
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
