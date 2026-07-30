"""Проверки применения миграций Alembic."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_upgrade_head_creates_initial_schema_on_clean_database(
    tmp_path,
    monkeypatch,
) -> None:
    """Alembic использует DATABASE_URL и создаёт таблицы на чистой SQLite-БД."""
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    project_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", "sqlite:///./wrong-location.db")
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        channel_columns = {
            column["name"] for column in inspector.get_columns("notification_channels")
        }
    finally:
        engine.dispose()

    assert table_names == {
        "alembic_version",
        "events",
        "leads",
        "notification_channels",
    }
    assert {
        "config_revision",
        "verified_config_revision",
        "verified_at",
    }.issubset(channel_columns)
