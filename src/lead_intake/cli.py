"""Локальные команды администрирования Lead Intake Service."""

import argparse
import secrets

from cryptography.fernet import Fernet


def build_parser() -> argparse.ArgumentParser:
    """Создать парсер аргументов консольной утилиты."""
    parser = argparse.ArgumentParser(prog="lead-intake")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "generate-fernet-key",
        help="Сгенерировать ключ для SECRETS_KEY.",
    )
    subparsers.add_parser(
        "generate-api-key",
        help="Сгенерировать ключ для API_KEY.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Выполнить выбранную локальную команду и вернуть код завершения."""
    arguments = build_parser().parse_args(argv)
    if arguments.command == "generate-fernet-key":
        print(Fernet.generate_key().decode("ascii"))
        return 0
    if arguments.command == "generate-api-key":
        print(secrets.token_urlsafe(32))
        return 0

    raise RuntimeError("Неизвестная команда.")
