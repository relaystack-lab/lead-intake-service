"""Проверки шифрования секретных полей."""

import pytest
from cryptography.fernet import Fernet

from lead_intake.security import (
    ENCRYPTED_VALUE_PREFIX,
    create_fernet,
    decrypt_channel_config,
    decrypt_secret,
    encrypt_channel_config,
    encrypt_secret,
)


@pytest.fixture
def fernet() -> Fernet:
    """Создать новый Fernet-шифратор для каждого теста."""
    secrets_key = Fernet.generate_key().decode("utf-8")
    return create_fernet(secrets_key)


def test_secret_is_encrypted_and_decrypted(fernet: Fernet) -> None:
    """Зашифрованная строка не содержит исходное значение."""
    encrypted_value = encrypt_secret("bot-token-value", fernet)

    assert encrypted_value.startswith(ENCRYPTED_VALUE_PREFIX)
    assert "bot-token-value" not in encrypted_value
    assert decrypt_secret(encrypted_value, fernet) == "bot-token-value"


def test_channel_config_encrypts_only_known_secret_fields(fernet: Fernet) -> None:
    """Конфигурация Telegram шифрует токен и сохраняет открытые поля."""
    config = {"bot_token": "bot-token-value", "chat_ids": ["123456"]}

    encrypted_config = encrypt_channel_config("telegram", config, fernet)

    assert encrypted_config["bot_token"] != config["bot_token"]
    assert encrypted_config["chat_ids"] == ["123456"]
    assert decrypt_channel_config("telegram", encrypted_config, fernet) == config


def test_decrypt_secret_rejects_unknown_format(fernet: Fernet) -> None:
    """Расшифровка отклоняет значение без маркера Fernet."""
    with pytest.raises(ValueError, match="префикса"):
        decrypt_secret("plain-text", fernet)
