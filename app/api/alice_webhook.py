"""Адаптер Яндекс Диалогов: разбирает запрос платформы, зовёт сервис, собирает ответ.

Игровая логика о формате Алисы ничего не знает.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.schemas.alice import AliceRequest, build_response
from app.service import GameService

logger = logging.getLogger("iskorka")

FRIENDLY_ERROR = "Ой, у Искорки запутались лапки. Давай попробуем ещё раз."
NO_IDENTITY = "Ой, что-то пошло не так. Попробуй позвать Искорку чуть позже."
PING_OK = "Искорка на месте. Всё хорошо!"


def create_router(service: GameService, max_body_bytes: int, secret: str = "") -> APIRouter:
    router = APIRouter()
    path = f"/alice/{secret}" if secret else "/alice"

    @router.post(path)
    async def alice_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > max_body_bytes:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            raw = json.loads(body)
            if not isinstance(raw, dict):
                raise ValueError("ожидался JSON-объект")
            incoming = AliceRequest.model_validate(raw)
        except (ValueError, ValidationError):
            # Платформа такие запросы не шлёт, поэтому голосовой ответ не нужен.
            return JSONResponse({"error": "bad_request"}, status_code=400)

        if incoming.is_ping():
            return JSONResponse(build_response(PING_OK, [], False))

        player_id = incoming.player_id()
        if not player_id:
            logger.warning("В запросе нет идентификатора приложения")
            return JSONResponse(build_response(NO_IDENTITY, [], True))

        try:
            outcome = await run_in_threadpool(
                service.handle_turn,
                player_id=player_id,
                session_id=incoming.session.session_id,
                message_id=incoming.session.message_id,
                utterance=incoming.utterance(),
                new_session=incoming.session.new,
                dangerous=incoming.is_dangerous(),
            )
        except Exception as exc:  # noqa: BLE001 — ребёнок не должен слышать технические детали
            logger.error("Ошибка обработки хода: %s", type(exc).__name__)
            return JSONResponse(build_response(FRIENDLY_ERROR, [], False))

        return JSONResponse(build_response(outcome.text, outcome.buttons, outcome.end_session))

    return router
