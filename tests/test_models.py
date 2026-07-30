"""Проверки ORM-моделей сервиса."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from lead_intake.models import (
    Base,
    Event,
    Lead,
    LeadStatus,
    NotificationChannel,
    NotificationChannelType,
)


@pytest.fixture
def db_session(tmp_path) -> Iterator[Session]:
    """Создать изолированную БД со схемой ORM-моделей."""
    database_path = tmp_path / "models.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_lead_event_and_notification_channel_are_persisted(
    db_session: Session,
) -> None:
    """Модели сохраняют заявку, событие и конфигурацию канала."""
    lead = Lead(
        name="Анна",
        contact="+79990000000",
        comment="Нужна консультация",
        source="website",
    )
    db_session.add(lead)
    db_session.flush()

    event = Event(
        lead_id=lead.id,
        type="lead_received",
        payload={"source": "website"},
    )
    channel = NotificationChannel(
        type=NotificationChannelType.TELEGRAM,
        config={"chat_ids": ["123456"]},
    )
    db_session.add_all([event, channel])
    db_session.commit()

    assert lead.id is not None
    assert lead.status is LeadStatus.NEW
    assert lead.created_at is not None
    assert event.id is not None
    assert event.payload == {"source": "website"}
    assert channel.id is not None
    assert channel.enabled is True
