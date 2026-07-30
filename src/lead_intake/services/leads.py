"""Бизнес-логика создания, дедупликации и выборки заявок."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lead_intake.models import Event, Lead, LeadStatus
from lead_intake.schemas import LeadCreate

DUPLICATE_WINDOW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class LeadCreationResult:
    """Заявка и признак, была ли она найдена как повторная."""

    lead: Lead
    duplicate: bool


@dataclass(frozen=True, slots=True)
class LeadPage:
    """Страница заявок и общее число подходящих записей."""

    items: list[Lead]
    total: int


def create_or_get_lead(session: Session, payload: LeadCreate) -> LeadCreationResult:
    """Создать заявку или вернуть существующую за последние пять минут."""
    cutoff = datetime.now(UTC) - DUPLICATE_WINDOW
    existing_lead = session.scalar(
        select(Lead)
        .where(
            Lead.contact == payload.contact,
            Lead.comment == payload.comment,
            Lead.created_at >= cutoff,
        )
        .order_by(Lead.created_at.desc())
        .limit(1)
    )
    if existing_lead is not None:
        return LeadCreationResult(lead=existing_lead, duplicate=True)

    lead = Lead(
        name=payload.name,
        contact=payload.contact,
        comment=payload.comment,
        source=payload.source,
    )
    session.add(lead)
    session.flush()
    session.add(
        Event(
            lead_id=lead.id,
            type="lead_received",
            payload={"source": lead.source},
        )
    )
    session.commit()
    session.refresh(lead)
    return LeadCreationResult(lead=lead, duplicate=False)


def get_leads(
    session: Session,
    page: int,
    page_size: int,
    source: str | None,
    lead_status: LeadStatus | None,
) -> LeadPage:
    """Вернуть страницу заявок с фильтрами по источнику и статусу."""
    filters = []
    normalized_source = source.strip().lower() if source is not None else ""
    if normalized_source:
        escaped_source = (
            normalized_source.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        filters.append(func.lower(Lead.source).like(f"%{escaped_source}%", escape="\\"))
    if lead_status is not None:
        filters.append(Lead.status == lead_status)

    statement = (
        select(Lead).where(*filters).order_by(Lead.created_at.desc(), Lead.id.desc())
    )
    total = session.scalar(select(func.count()).select_from(Lead).where(*filters))
    items = list(
        session.scalars(statement.offset((page - 1) * page_size).limit(page_size))
    )
    return LeadPage(items=items, total=total or 0)


def update_lead_status(
    session: Session,
    lead_id: int,
    new_status: LeadStatus,
) -> Lead | None:
    """Обновить статус заявки и сохранить событие изменения в event log."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        return None

    previous_status = lead.status
    if previous_status is not new_status:
        lead.status = new_status
        session.add(
            Event(
                lead_id=lead.id,
                type="lead_status_changed",
                payload={"from": previous_status.value, "to": new_status.value},
            )
        )
        session.commit()
        session.refresh(lead)
    return lead
