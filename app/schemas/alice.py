"""Модели входящего запроса Яндекс Диалогов (протокол 1.0) и сборка ответа.

Поля и лимиты взяты из официальной документации «Формат запроса» / «Формат ответа».
Неизвестные поля игнорируются: платформа может их добавлять.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Markup(_Lenient):
    dangerous_context: bool | None = None


class RequestBody(_Lenient):
    type: str = Field(max_length=32)
    command: str = Field(default="", max_length=1024)
    original_utterance: str = Field(default="", max_length=1024)
    markup: Markup | None = None
    payload: dict[str, Any] | None = None


class Application(_Lenient):
    application_id: str | None = Field(default=None, max_length=64)


class SessionUser(_Lenient):
    user_id: str | None = Field(default=None, max_length=64)


class Session(_Lenient):
    session_id: str = Field(min_length=1, max_length=64)
    message_id: int | None = None
    skill_id: str | None = Field(default=None, max_length=64)
    user_id: str | None = Field(default=None, max_length=64)  # устарел, используется как запасной
    user: SessionUser | None = None
    application: Application | None = None
    new: bool = False


class AliceRequest(_Lenient):
    request: RequestBody
    session: Session
    version: str = Field(default="1.0", max_length=16)

    def player_id(self) -> str | None:
        """Идентификатор экземпляра приложения (стабилен для устройства/приложения).

        Запасной вариант — устаревший session.user_id: по документации его значение
        совпадает с application_id.
        """
        if self.session.application and self.session.application.application_id:
            return self.session.application.application_id
        return self.session.user_id or None

    def user_id(self) -> str | None:
        """Подтверждённый идентификатор пользователя Яндекса: session.user.user_id.

        Есть только у авторизованных. Устаревшее плоское session.user_id (равно application_id),
        session_id и application_id постоянным идентификатором пользователя не считаются.
        """
        user = self.session.user
        value = (user.user_id or "").strip() if user else ""
        return value or None

    def utterance(self) -> str:
        return self.request.command or self.request.original_utterance

    def is_ping(self) -> bool:
        return self.request.original_utterance.strip().lower() == "ping"

    def is_dangerous(self) -> bool:
        return bool(self.request.markup and self.request.markup.dangerous_context)


def build_response(text: str, buttons: list[str], end_session: bool, tts: str | None = None) -> dict:
    """Собирает ответ платформы.

    `text` — обязательное поле протокола (показывается на экране, ≤1024 знаков), поэтому
    остаётся. Озвучка — `tts` (с тегами звуков); без неё Алиса читает `text`.
    """
    response: dict[str, Any] = {"text": text, "tts": tts if tts else text, "end_session": end_session}
    if buttons and not end_session:
        response["buttons"] = [{"title": title[:64], "hide": True} for title in buttons]
    return {"response": response, "version": "1.0"}
