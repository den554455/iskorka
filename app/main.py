"""Точка входа. Запуск: uvicorn app.main:create_app --factory"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.alice_webhook import create_router
from app.config import Settings, get_settings
from app.db.database import Database
from app.game.engine import build_multi_engine
from app.logging_setup import configure_logging, new_request_id, request_id_var, safe_path
from app.service import GameService

logger = logging.getLogger("iskorka")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.webhook_secret)

    engine = build_multi_engine()  # падает при ошибке в сценарии любого эпизода
    db = Database(settings.database_url)
    db.create_schema()
    service = GameService(db, engine, settings.player_key_pepper)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info(
            "Приложение запущено: эпизодов=%d, БД=%s, уровень логов=%s",
            len(engine.engines), db.engine.dialect.name, logging.getLevelName(logging.getLogger().level),
        )
        yield
        logger.info("Приложение остановлено")

    app = FastAPI(title="Искорка", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service = service
    app.state.db = db
    secret = settings.webhook_secret

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        """Request id, время обработки и одна строка на запрос. Исключения, не пойманные
        обработчиком webhook, логируются здесь — один раз, с traceback."""
        rid = new_request_id(request.headers.get("x-request-id"))
        token = request_id_var.set(rid)
        started = time.perf_counter()
        path = safe_path(request.url.path + (f"?{request.url.query}" if request.url.query else ""), secret)
        try:
            response = await call_next(request)
        except Exception:
            ms = (time.perf_counter() - started) * 1000
            logger.exception("Необработанное исключение: %s %s (%.1f мс)", request.method, path, ms)
            response = JSONResponse({"error": "internal_error", "request_id": rid}, status_code=500)
        else:
            ms = (time.perf_counter() - started) * 1000
            level = logging.INFO if response.status_code < 500 else logging.ERROR
            logger.log(level, "%s %s -> %d (%.1f мс)", request.method, path, response.status_code, ms)
        response.headers["X-Request-Id"] = rid
        request_id_var.reset(token)
        return response

    app.include_router(create_router(service, settings.max_body_bytes, settings.webhook_secret))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
