"""Интеграционные проверки API приёма заявок."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from lead_intake.models import Event, Lead, LeadStatus

API_HEADERS = {"X-API-Key": "test-api-key"}


def test_openapi_documents_leads_and_tilda_endpoints(client: TestClient) -> None:
    """OpenAPI содержит только интеграционные маршруты с нужными контрактами."""
    docs_response = client.get("/docs")
    openapi_response = client.get("/openapi.json")
    schema = openapi_response.json()
    tilda_operation = schema["paths"]["/api/v1/leads/tilda"]["post"]

    assert docs_response.status_code == 200
    assert openapi_response.status_code == 200
    assert "/api/v1/leads" in schema["paths"]
    assert "/api/v1/leads/tilda" in schema["paths"]
    assert "/admin/leads" not in schema["paths"]
    assert tilda_operation["security"] == [{"TildaApiKey": []}]
    assert set(tilda_operation["requestBody"]["content"]) == {
        "application/json",
        "application/x-www-form-urlencoded",
    }
    assert (
        "Phone"
        in tilda_operation["requestBody"]["content"][
            "application/x-www-form-urlencoded"
        ]["schema"]["properties"]
    )
    assert schema["paths"]["/api/v1/leads"]["post"]["security"] == [
        {"ApiKeyHeader": []}
    ]


def test_create_lead_records_received_event(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Новая заявка сохраняется вместе с событием получения."""
    response = client.post(
        "/api/v1/leads",
        headers=API_HEADERS,
        json={
            "name": "Анна",
            "contact": "+79990000000",
            "comment": "Нужна консультация",
            "source": "website",
        },
    )

    session = app.state.session_factory()
    try:
        event = session.scalar(select(Event))
    finally:
        session.close()

    assert response.status_code == 201
    assert response.json() == {"id": 1, "duplicate": False}
    assert event is not None
    assert event.type == "lead_received"
    assert event.payload == {"source": "website"}


def test_duplicate_lead_returns_existing_id(client: TestClient) -> None:
    """Повтор contact и comment в окне дедупликации не создаёт новую заявку."""
    payload = {
        "contact": "+79990000000",
        "comment": "Нужна консультация",
        "source": "website",
    }

    first_response = client.post("/api/v1/leads", headers=API_HEADERS, json=payload)
    duplicate_response = client.post("/api/v1/leads", headers=API_HEADERS, json=payload)

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 200
    assert duplicate_response.json() == {"id": 1, "duplicate": True}


def test_create_lead_rejects_invalid_payload_and_api_key(client: TestClient) -> None:
    """API возвращает 401 без ключа и 422 для пустого обязательного контакта."""
    unauthorized_response = client.post(
        "/api/v1/leads", json={"contact": "+79990000000"}
    )
    invalid_response = client.post(
        "/api/v1/leads",
        headers=API_HEADERS,
        json={"contact": "   ", "comment": "x"},
    )
    too_long_contact_response = client.post(
        "/api/v1/leads",
        headers=API_HEADERS,
        json={"contact": "x" * 256},
    )

    assert unauthorized_response.status_code == 401
    assert invalid_response.status_code == 422
    assert too_long_contact_response.status_code == 422


def test_list_leads_supports_pagination_and_filters(
    app: FastAPI,
    client: TestClient,
) -> None:
    """Список фильтруется по источнику и статусу, а также разбивается на страницы."""
    for contact, source in [
        ("+79990000001", "website"),
        ("+79990000002", "tilda"),
        ("+79990000003", "website"),
    ]:
        client.post(
            "/api/v1/leads",
            headers=API_HEADERS,
            json={"contact": contact, "source": source},
        )

    session = app.state.session_factory()
    try:
        lead = session.scalar(select(Lead).where(Lead.contact == "+79990000003"))
        assert lead is not None
        lead.status = LeadStatus.DONE
        session.commit()
    finally:
        session.close()

    source_response = client.get(
        "/api/v1/leads?source=WEB&page=1&page_size=1",
        headers=API_HEADERS,
    )
    status_response = client.get(
        "/api/v1/leads?source=&status=done",
        headers=API_HEADERS,
    )
    all_statuses_response = client.get(
        "/api/v1/leads?source=web&status=",
        headers=API_HEADERS,
    )

    assert source_response.status_code == 200
    assert source_response.json()["total"] == 2
    assert len(source_response.json()["items"]) == 1
    assert status_response.json()["total"] == 1
    assert status_response.json()["items"][0]["contact"] == "+79990000003"
    assert all_statuses_response.status_code == 200
    assert all_statuses_response.json()["total"] == 2


def test_tilda_adapter_maps_urlencoded_fields_and_returns_success(
    client: TestClient,
) -> None:
    """Адаптер Tilda маппит поля формы и запускает доставку новой заявки."""
    with patch("lead_intake.api.adapters.tilda.dispatch_notifications") as dispatch:
        response = client.post(
            "/api/v1/leads/tilda?api_key=test-api-key",
            data={
                "Name": "Иван",
                "Phone": "+79990000004",
                "Comments": "Перезвоните",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"id": 1, "duplicate": False}
    dispatch.assert_called_once()


def test_tilda_adapter_returns_bad_request_without_contact(client: TestClient) -> None:
    """Адаптер Tilda сообщает 400, если в форме нет телефона или email."""
    response = client.post(
        "/api/v1/leads/tilda?api_key=test-api-key",
        data={"Name": "Иван"},
    )

    assert response.status_code == 400


def test_tilda_adapter_rejects_invalid_contact_with_422(client: TestClient) -> None:
    """Адаптер Tilda не превращает ошибку валидации заявки во внутреннюю ошибку."""
    response = client.post(
        "/api/v1/leads/tilda?api_key=test-api-key",
        data={"Phone": "x" * 256},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Request validation failed"}


def test_api_lead_and_received_event_are_visible_in_admin(client: TestClient) -> None:
    """Заявка из API и её исходное событие доступны через административный интерфейс."""
    response = client.post(
        "/api/v1/leads",
        headers=API_HEADERS,
        json={"contact": "+79990000005", "source": "website"},
    )
    lead_id = response.json()["id"]

    leads_response = client.get("/admin/leads", auth=("admin", "admin-password"))
    events_response = client.get(
        f"/admin/leads/{lead_id}/events",
        auth=("admin", "admin-password"),
    )

    assert leads_response.status_code == 200
    assert "+79990000005" in leads_response.text
    assert events_response.status_code == 200
    assert "lead_received" in events_response.text
    assert "website" in events_response.text


def test_tilda_adapter_rejects_invalid_query_api_key(client: TestClient) -> None:
    """Адаптер Tilda требует корректный ключ в query-параметре."""
    response = client.post(
        "/api/v1/leads/tilda?api_key=wrong-key",
        data={"Phone": "+79990000004"},
    )

    assert response.status_code == 401


def test_unexpected_error_returns_generic_json_response(client: TestClient) -> None:
    """Непредвиденная ошибка не раскрывает клиенту внутренние подробности."""
    with patch(
        "lead_intake.api.leads.create_or_get_lead",
        side_effect=RuntimeError("database password must stay private"),
    ):
        response = client.post(
            "/api/v1/leads",
            headers=API_HEADERS,
            json={"contact": "+79990000000"},
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
