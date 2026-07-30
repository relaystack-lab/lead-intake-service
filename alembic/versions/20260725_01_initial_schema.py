"""Создать начальную схему заявок и уведомлений.

Revision ID: 20260725_01
Revises:
Create Date: 2026-07-25 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260725_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать таблицы первой версии схемы."""
    lead_status = sa.Enum(
        "new",
        "in_progress",
        "done",
        name="lead_status",
        native_enum=False,
        create_constraint=True,
    )
    notification_channel_type = sa.Enum(
        "telegram",
        "email",
        name="notification_channel_type",
        native_enum=False,
        create_constraint=True,
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("contact", sa.String(length=255), nullable=False),
        sa.Column("comment", sa.Text(), server_default="", nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("status", lead_status, server_default="new", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_leads_contact", "leads", ["contact"], unique=False)
    op.create_index("ix_leads_source", "leads", ["source"], unique=False)
    op.create_index("ix_leads_status", "leads", ["status"], unique=False)

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_lead_id", "events", ["lead_id"], unique=False)
    op.create_index("ix_events_type", "events", ["type"], unique=False)

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", notification_channel_type, nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type"),
    )


def downgrade() -> None:
    """Удалить таблицы первой версии схемы."""
    op.drop_table("notification_channels")
    op.drop_index("ix_events_type", table_name="events")
    op.drop_index("ix_events_lead_id", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_leads_status", table_name="leads")
    op.drop_index("ix_leads_source", table_name="leads")
    op.drop_index("ix_leads_contact", table_name="leads")
    op.drop_table("leads")
