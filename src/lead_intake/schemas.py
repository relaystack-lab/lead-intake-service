"""Pydantic-схемы HTTP API заявок."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lead_intake.models import LeadStatus


class LeadCreate(BaseModel):
    """Данные новой заявки из внешнего источника."""

    name: str | None = Field(default=None, max_length=255)
    contact: str = Field(max_length=255)
    comment: str = Field(default="", max_length=5000)
    source: str = Field(default="api", max_length=100)

    @field_validator("contact", "source")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        """Удалить внешние пробелы и запретить пустые обязательные поля."""
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("Поле не может быть пустым.")
        return normalized_value


class LeadCreateResponse(BaseModel):
    """Результат создания или обнаружения повторной заявки."""

    id: int
    duplicate: bool


class LeadRead(BaseModel):
    """Публичное представление заявки в списке API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    contact: str
    comment: str
    source: str
    status: LeadStatus
    created_at: datetime
    updated_at: datetime


class LeadListResponse(BaseModel):
    """Страница списка заявок с метаданными пагинации."""

    items: list[LeadRead]
    page: int
    page_size: int
    total: int
