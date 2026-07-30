"""HTTP API создания и получения списка заявок."""

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from lead_intake.db import get_session
from lead_intake.models import LeadStatus
from lead_intake.schemas import LeadCreate, LeadCreateResponse, LeadListResponse
from lead_intake.security import verify_api_key
from lead_intake.services.leads import create_or_get_lead, get_leads
from lead_intake.services.notifications.dispatcher import dispatch_notifications

router = APIRouter(prefix="/api/v1/leads", tags=["Leads"])


@router.post("", response_model=LeadCreateResponse, status_code=status.HTTP_201_CREATED)
def create_lead(
    payload: LeadCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[None, Depends(verify_api_key)],
) -> LeadCreateResponse:
    """Принять новую заявку с защитой от повтора в течение пяти минут."""
    result = create_or_get_lead(session, payload)
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    else:
        background_tasks.add_task(
            dispatch_notifications,
            request.app.state.session_factory,
            request.app.state.settings,
            result.lead.id,
        )

    return LeadCreateResponse(id=result.lead.id, duplicate=result.duplicate)


@router.get("", response_model=LeadListResponse)
def list_leads(
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[None, Depends(verify_api_key)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    source: Annotated[str | None, Query(max_length=100)] = None,
    status_value: Annotated[str | None, Query(alias="status")] = None,
) -> LeadListResponse:
    """Вернуть страницу заявок с фильтрами источника и статуса."""
    try:
        lead_status = LeadStatus(status_value) if status_value else None
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT
        ) from error
    result = get_leads(session, page, page_size, source, lead_status)
    return LeadListResponse(
        items=result.items,
        page=page,
        page_size=page_size,
        total=result.total,
    )
