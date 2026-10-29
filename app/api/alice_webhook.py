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
from sqlalchemy.exc import SQLAlchemyError

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
        except (ValueError, ValidationError) as exc:
            # Платформа такие запросы не шлёт, поэтому голосовой ответ не нужен.
            # Тело запроса в журнал не пишем: в нём может быть речь ребёнка.
            logger.warning("Некорректный запрос webhook: %s (%d байт)", type(exc).__name__, len(body))
            return JSONResponse({"error": "bad_request"}, status_code=400)

        if incoming.is_ping():
            return JSONResponse(build_response(PING_OK, [], False))

        player_id = incoming.player_id()
        user_id = incoming.user_id()
        if not player_id and not user_id:
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
                user_id=user_id,
            )
        except SQLAlchemyError:
            # Единственная запись об ошибке (с traceback): повторно её не логируем ни в сервисе, ни в middleware.
            logger.exception("Ошибка БД при обработке хода (транзакция отменена)")
            return JSONResponse(build_response(FRIENDLY_ERROR, [], False))
        except Exception:  # noqa: BLE001 — ребёнок не должен слышать технические детали
            logger.exception("Ошибка обработки хода (сценарий или сервис)")
            return JSONResponse(build_response(FRIENDLY_ERROR, [], False))

        try:
            body_out = build_response(outcome.text, outcome.buttons, outcome.end_session, tts=outcome.tts)
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка формирования ответа Алисе")
            return JSONResponse(build_response(FRIENDLY_ERROR, [], False))
        return JSONResponse(body_out)

    return router
