"""Шифрование секретов каналов уведомлений."""

import secrets
from collections.abc import Callable, Mapping
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, APIKeyQuery, HTTPBasic, HTTPBasicCredentials
from pydantic import SecretStr

ENCRYPTED_VALUE_PREFIX = "fernet:"
SECRET_CONFIG_FIELDS: dict[str, frozenset[str]] = {
    "telegram": frozenset({"bot_token"}),
    "email": frozenset({"smtp_password"}),
}
admin_security = HTTPBasic(auto_error=False)
api_key_header_security = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ApiKeyHeader",
    auto_error=False,
)
tilda_api_key_security = APIKeyQuery(
    name="api_key",
    scheme_name="TildaApiKey",
    auto_error=False,
)


def create_fernet(secrets_key: str) -> Fernet:
    """Создать Fernet-шифратор из ключа, полученного из окружения."""
    return Fernet(secrets_key.encode("utf-8"))


def get_fernet(secrets_key: SecretStr | None) -> Fernet:
    """Создать шифратор из настройки или сообщить об отсутствии ключа."""
    if secrets_key is None:
        raise ValueError("SECRETS_KEY is not configured")
    return create_fernet(secrets_key.get_secret_value())


def verify_api_key(
    request: Request,
    x_api_key: str | None = Security(api_key_header_security),
) -> None:
    """Проверить ключ доступа из заголовка X-API-Key."""
    configured_key = request.app.state.settings.api_key
    if configured_key is None or x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    if not secrets.compare_digest(x_api_key, configured_key.get_secret_value()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )


def verify_tilda_api_key(
    request: Request,
    api_key: str | None = Security(tilda_api_key_security),
) -> None:
    """Проверить ключ доступа адаптера Tilda из query-параметра."""
    configured_key = request.app.state.settings.api_key
    if configured_key is None or api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    if not secrets.compare_digest(api_key, configured_key.get_secret_value()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )


def require_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(admin_security),
) -> None:
    """Проверить HTTP Basic-учётные данные административного интерфейса."""
    configured_username = request.app.state.settings.admin_username
    configured_password = request.app.state.settings.admin_password
    valid_credentials = (
        credentials is not None
        and configured_username is not None
        and configured_password is not None
        and secrets.compare_digest(credentials.username, configured_username)
        and secrets.compare_digest(
            credentials.password, configured_password.get_secret_value()
        )
    )
    if not valid_credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": 'Basic realm="Lead Intake Admin"'},
        )


def encrypt_secret(value: str, fernet: Fernet) -> str:
    """Зашифровать строковое значение и пометить его формат хранения."""
    encrypted_value = fernet.encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_VALUE_PREFIX}{encrypted_value}"


def decrypt_secret(value: str, fernet: Fernet) -> str:
    """Расшифровать значение Fernet и отклонить нераспознанный формат."""
    if not value.startswith(ENCRYPTED_VALUE_PREFIX):
        message = "Значение не содержит ожидаемого префикса шифрования."
        raise ValueError(message)

    encrypted_value = value.removeprefix(ENCRYPTED_VALUE_PREFIX)
    try:
        return fernet.decrypt(encrypted_value.encode("utf-8")).decode("utf-8")
    except InvalidToken as error:
        raise ValueError("Не удалось расшифровать значение.") from error


def encrypt_channel_config(
    channel_type: str,
    config: Mapping[str, Any],
    fernet: Fernet,
) -> dict[str, Any]:
    """Зашифровать известные секретные поля конфигурации канала."""
    return _transform_channel_config(channel_type, config, fernet, encrypt_secret)


def decrypt_channel_config(
    channel_type: str,
    config: Mapping[str, Any],
    fernet: Fernet,
) -> dict[str, Any]:
    """Расшифровать известные секретные поля конфигурации канала."""
    return _transform_channel_config(channel_type, config, fernet, decrypt_secret)


def _transform_channel_config(
    channel_type: str,
    config: Mapping[str, Any],
    fernet: Fernet,
    transform: Callable[[str, Fernet], str],
) -> dict[str, Any]:
    """Применить преобразование только к секретным строковым полям канала."""
    transformed_config = dict(config)
    for field_name in SECRET_CONFIG_FIELDS.get(channel_type, frozenset()):
        field_value = transformed_config.get(field_name)
        if field_value is None:
            continue
        if not isinstance(field_value, str):
            message = f"Поле {field_name} должно быть строкой."
            raise ValueError(message)
        transformed_config[field_name] = transform(field_value, fernet)

    return transformed_config
