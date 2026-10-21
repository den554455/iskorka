"""Настройки приложения. Читаются из переменных окружения (и из .env, если он есть)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

logger = logging.getLogger("iskorka")

DEFAULT_PEPPER = "dev-pepper-change-me"


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/iskorka.db"
    # Секретная «соль» для хэширования идентификатора игрока. В бою задать свою.
    player_key_pepper: str = DEFAULT_PEPPER
    # Если задан, webhook доступен по /alice/<секрет>, а не по /alice.
    webhook_secret: str = ""
    max_body_bytes: int = 65536
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Собирает настройки из окружения. Вызывается при создании приложения."""
    load_dotenv()
    settings = Settings(
        database_url=os.environ.get("DATABASE_URL", Settings.database_url),
        player_key_pepper=os.environ.get("PLAYER_KEY_PEPPER", DEFAULT_PEPPER),
        webhook_secret=os.environ.get("WEBHOOK_SECRET", ""),
        max_body_bytes=int(os.environ.get("MAX_BODY_BYTES", Settings.max_body_bytes)),
        log_level=os.environ.get("LOG_LEVEL", Settings.log_level),
    )
    if settings.player_key_pepper == DEFAULT_PEPPER:
        logger.warning("PLAYER_KEY_PEPPER не задан: используется значение для разработки.")
    return settings
