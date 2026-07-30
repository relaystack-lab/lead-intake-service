"""Настройки приложения из переменных окружения и файла .env."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки, необходимые для запуска сервиса."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./data/lead_intake.db"
    api_key: SecretStr | None = None
    admin_username: str | None = None
    admin_password: SecretStr | None = None
    secrets_key: SecretStr | None = None
    log_directory: str = "output/logs"
    log_retention_days: int = Field(default=30, ge=1)
    log_level: str = "INFO"
