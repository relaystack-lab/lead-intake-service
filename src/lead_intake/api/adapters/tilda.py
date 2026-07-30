"""Адаптер заявок из форм Tilda."""

import json
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from lead_intake.db import get_session
from lead_intake.schemas import LeadCreate, LeadCreateResponse
from lead_intake.security import verify_tilda_api_key
from lead_intake.services.leads import create_or_get_lead
from lead_intake.services.notifications.dispatcher import dispatch_notifications

router = APIRouter(prefix="/api/v1/leads/tilda", tags=["Tilda"])

TILDA_REQUEST_BODY = {
    "required": True,
    "content": {
        "application/x-www-form-urlencoded": {
            "schema": {
                "type": "object",
                "properties": {
                    "Name": {"type": "string", "example": "Иван"},
                    "Phone": {"type": "string", "example": "+79990000001"},
                    "Email": {
                        "type": "string",
                        "format": "email",
                        "example": "ivan@example.com",
                    },
                    "Comments": {
                        "type": "string",
                        "example": "Перезвоните после 18:00",
                    },
                },
            }
        },
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "Name": {"type": "string", "example": "Иван"},
                    "Phone": {"type": "string", "example": "+79990000001"},
                    "Email": {
                        "type": "string",
                        "format": "email",
                        "example": "ivan@example.com",
                    },
                    "Comments": {
                        "type": "string",
                        "example": "Перезвоните после 18:00",
                    },
                },
            }
        },
    },
}


@router.post(
    "",
    response_model=LeadCreateResponse,
    summary="Принять заявку из формы Tilda",
    responses={
        400: {"description": "В теле отсутствуют Phone и Email."},
        401: {"description": "Некорректный API-ключ."},
        422: {"description": "Данные заявки не прошли валидацию."},
        500: {"description": "Внутренняя ошибка сервиса."},
    },
    openapi_extra={"requestBody": TILDA_REQUEST_BODY},
)
async def accept_tilda_lead(
    request: Request,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[None, Depends(verify_tilda_api_key)],
) -> LeadCreateResponse:
    """Принять urlencoded или JSON-заявку Tilda и привести её к общей схеме."""
    payload = await _extract_payload(request)
    contact = _first_text(payload, "Phone", "phone", "Email", "email")
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tilda request must contain Phone or Email",
        )

    try:
        lead_payload = LeadCreate(
            name=_first_text(payload, "Name", "name"),
            contact=contact,
            comment=_first_text(
                payload,
                "Comments",
                "comments",
                "Comment",
                "comment",
            )
            or "",
            source="tilda",
        )
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error
    result = create_or_get_lead(session, lead_payload)
    if not result.duplicate:
        background_tasks.add_task(
            dispatch_notifications,
            request.app.state.session_factory,
            request.app.state.settings,
            result.lead.id,
        )
    return LeadCreateResponse(id=result.lead.id, duplicate=result.duplicate)


async def _extract_payload(request: Request) -> dict[str, Any]:
    """Извлечь данные из JSON или URL-encoded тела без внешнего парсера форм."""
    raw_body = await request.body()
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Malformed JSON body",
            ) from error
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="JSON body must be an object",
            )
        return payload

    parsed_payload = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed_payload.items()}


def _first_text(payload: dict[str, Any], *field_names: str) -> str | None:
    """Вернуть первое непустое строковое значение из известных имён поля."""
    for field_name in field_names:
        value = payload.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
