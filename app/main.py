"""Точка входа. Запуск: uvicorn app.main:create_app --factory"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.alice_webhook import create_router
from app.config import Settings, get_settings
from app.db.database import Database
from app.game.engine import build_multi_engine
from app.service import GameService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    engine = build_multi_engine()  # падает при ошибке в сценарии любого эпизода
    db = Database(settings.database_url)
    db.create_schema()
    service = GameService(db, engine, settings.player_key_pepper)

    app = FastAPI(title="Искорка", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.state.db = db
    app.include_router(create_router(service, settings.max_body_bytes, settings.webhook_secret))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
