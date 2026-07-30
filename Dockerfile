# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

RUN python -m venv /opt/venv

COPY pyproject.toml ./
COPY src ./src

RUN /opt/venv/bin/python -m pip install --upgrade pip \
    && /opt/venv/bin/python -m pip install .

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid appuser --create-home --shell /usr/sbin/nologin appuser \
    && mkdir /data \
    && chown appuser:appuser /data

COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn lead_intake.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
