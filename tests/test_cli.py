"""Проверки локальных консольных команд."""

import re

import pytest
from cryptography.fernet import Fernet

from lead_intake.cli import main


def test_generate_fernet_key_outputs_valid_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Команда выводит корректный Fernet-ключ и не меняет конфигурацию."""
    result = main(["generate-fernet-key"])
    output = capsys.readouterr().out.strip()

    assert result == 0
    Fernet(output.encode("ascii"))


def test_generate_api_key_outputs_url_safe_random_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Команда выводит URL-safe ключ, подходящий для переменной API_KEY."""
    result = main(["generate-api-key"])
    output = capsys.readouterr().out.strip()

    assert result == 0
    assert re.fullmatch(r"[A-Za-z0-9_-]+", output)
    assert len(output) >= 32
