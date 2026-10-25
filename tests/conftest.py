from __future__ import annotations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.content import load_episode
from app.db.database import Database
from app.game.engine import Engine, build_multi_engine
from app.main import create_app
from app.service import GameService

PEPPER = "test-pepper"


@pytest.fixture(scope="session")
def episode() -> dict:
    return load_episode()


@pytest.fixture(scope="session")
def multi_engine():
    return build_multi_engine()


@pytest.fixture
def engine(episode: dict) -> Engine:
    return Engine(episode)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    database.create_schema()
    yield database
    database.dispose()


@pytest.fixture
def service(db: Database, engine: Engine) -> GameService:
    return GameService(db, engine, PEPPER)


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'web.db'}", player_key_pepper=PEPPER, max_body_bytes=4096
    )
    with TestClient(create_app(settings)) as c:
        yield c


class Player:
    """Помощник: ведёт диалог через сервис, как это делала бы платформа."""

    def __init__(self, service: GameService, player_id: str = "player-1", session_id: str = "s1"):
        self.service = service
        self.player_id = player_id
        self.session_id = session_id
        self.message_id = 0
        self.new = True

    def say(self, utterance: str = "", new_session: bool | None = None):
        outcome = self.service.handle_turn(
            player_id=self.player_id,
            session_id=self.session_id,
            message_id=self.message_id,
            utterance=utterance,
            new_session=self.new if new_session is None else new_session,
        )
        self.message_id += 1
        self.new = False
        return outcome

    def new_session(self, session_id: str) -> None:
        self.session_id = session_id
        self.message_id = 0
        self.new = True


def alice_payload(
    utterance: str = "",
    *,
    application_id: str = "app-1",
    session_id: str = "sess-1",
    message_id: int = 0,
    new: bool = False,
    dangerous: bool = False,
) -> dict:
    """Запрос в формате протокола 1.0 (поля из официальной документации)."""
    request: dict = {"type": "SimpleUtterance", "command": utterance, "original_utterance": utterance}
    if dangerous:
        request["markup"] = {"dangerous_context": True}
    return {
        "meta": {"locale": "ru-RU", "timezone": "Europe/Moscow", "interfaces": {"screen": {}}},
        "request": request,
        "session": {
            "message_id": message_id,
            "session_id": session_id,
            "skill_id": "skill-test",
            "user_id": application_id,
            "application": {"application_id": application_id},
            "new": new,
        },
        "version": "1.0",
    }


def mentions_both_places(text: str) -> bool:
    """Вопрос про место назван обоими вариантами; форма слова зависит от варианта фразы."""
    low = text.lower()
    return "сад" in low and ("ручь" in low or "ручей" in low)
