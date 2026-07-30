"""Подключение к базе данных и фабрика сессий SQLAlchemy."""

from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def _prepare_sqlite_directory(database_url: str) -> None:
    """Создать родительскую папку файловой SQLite-БД при необходимости."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or database_url == "sqlite:///:memory:":
        return

    database_path = Path(database_url.removeprefix(prefix))
    database_path.parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(database_url: str) -> Engine:
    """Создать синхронный SQLAlchemy engine для заданной строки подключения."""
    _prepare_sqlite_directory(database_url)

    connect_args = (
        {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    )
    return create_engine(database_url, connect_args=connect_args)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Создать фабрику сессий, привязанную к engine приложения."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_session(request: Request) -> Generator[Session, None, None]:
    """Предоставить сессию БД на время обработки одного HTTP-запроса."""
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
