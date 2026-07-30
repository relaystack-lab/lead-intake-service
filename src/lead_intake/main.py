"""Создание и запуск FastAPI-приложения."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from lead_intake.admin.views import router as admin_router
from lead_intake.api.adapters.tilda import router as tilda_router
from lead_intake.api.leads import router as leads_router
from lead_intake.config import Settings
from lead_intake.db import create_db_engine, create_session_factory
from lead_intake.logging_config import configure_logging, request_log_extra


def create_app(settings: Settings | None = None) -> FastAPI:
    """Создать приложение с отдельным подключением к базе данных."""
    app_settings = settings or Settings()
    engine = create_db_engine(app_settings.database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        yield
        application.state.engine.dispose()

    app = FastAPI(title="Lead Intake Service", lifespan=lifespan)
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.settings = app_settings
    app.state.logger = configure_logging(
        app_settings.log_directory,
        app_settings.log_retention_days,
        app_settings.log_level,
    )

    @app.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Присвоить запросу идентификатор, не принимая его извне."""
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        """Зафиксировать безопасную причину ошибки валидации без входных данных."""
        reason, field_name = _validation_error_details(error)
        request.app.state.logger.warning(
            "Request rejected",
            extra=request_log_extra(
                request,
                event="request_rejected",
                category="validation_rejected",
                reason=reason,
                field=field_name,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "Request validation failed"},
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(
        request: Request,
        error: HTTPException,
    ) -> JSONResponse:
        """Сохранить безопасные сведения об отказах авторизации и плохих запросах."""
        if error.status_code in {
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
        }:
            category = (
                "authentication_failed"
                if error.status_code == status.HTTP_401_UNAUTHORIZED
                else "validation_rejected"
            )
            reason = _http_error_reason(error)
            request.app.state.logger.warning(
                "Request rejected",
                extra=request_log_extra(
                    request,
                    event="request_rejected",
                    category=category,
                    reason=reason,
                    status_code=error.status_code,
                ),
            )
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail},
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        """Вернуть нейтральный JSON-ответ при непредвиденной ошибке."""
        request.app.state.logger.error(
            "Unhandled request error",
            extra=request_log_extra(
                request,
                event="server_error",
                category="internal_error",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                exception_type=type(error).__name__,
            ),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    @app.get("/health")
    def health(request: Request) -> JSONResponse:
        """Проверить доступность соединения с базой данных."""
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            request.app.state.logger.warning(
                "Database health check failed",
                extra=request_log_extra(
                    request,
                    event="health_check_failed",
                    category="database_unavailable",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                ),
            )
            return JSONResponse(status_code=503, content={"status": "unavailable"})

        return JSONResponse(content={"status": "ok"})

    app.include_router(leads_router)
    app.include_router(tilda_router)
    app.include_router(admin_router)

    return app


def _validation_error_details(error: RequestValidationError) -> tuple[str, str]:
    """Вернуть категорию и имя поля, не записывая его значение в лог."""
    first_error = error.errors()[0]
    location = first_error.get("loc", ())
    field_name = str(location[-1]) if location else "request"
    error_type = first_error.get("type")
    if error_type == "json_invalid":
        return "body_not_json", "body"
    if field_name == "contact":
        raw_value = first_error.get("input")
        if isinstance(raw_value, str) and not raw_value.strip():
            return "contact_empty", field_name
        if error_type == "string_too_long":
            return "contact_too_long", field_name
        if error_type == "missing":
            return "contact_missing", field_name
        return "contact_invalid", field_name
    return "request_invalid", field_name


def _http_error_reason(error: HTTPException) -> str:
    """Преобразовать статическую HTTP-ошибку в безопасную категорию лога."""
    if error.status_code == status.HTTP_401_UNAUTHORIZED:
        return "authentication_failed"
    if error.detail == "Malformed JSON body":
        return "body_not_json"
    if error.detail == "JSON body must be an object":
        return "body_not_object"
    if error.detail == "Tilda request must contain Phone or Email":
        return "contact_missing"
    return "bad_request"


app = create_app()
