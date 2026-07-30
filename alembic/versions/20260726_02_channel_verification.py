"""Добавить состояние проверки настроек каналов.

Revision ID: 20260726_02
Revises: 20260725_01
Create Date: 2026-07-26 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260726_02"
down_revision: str | Sequence[str] | None = "20260725_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавить версию конфигурации и дату успешной проверки."""
    op.add_column(
        "notification_channels",
        sa.Column(
            "config_revision",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "notification_channels",
        sa.Column("verified_config_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "notification_channels",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Удалить состояние проверки настроек каналов."""
    op.drop_column("notification_channels", "verified_at")
    op.drop_column("notification_channels", "verified_config_revision")
    op.drop_column("notification_channels", "config_revision")
