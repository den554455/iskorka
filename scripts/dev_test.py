"""Локальная проверка диалога без Алисы.

Примеры (из корня проекта, с активированным окружением):

  # Интерактивно, напрямую через игровой сервис и вашу локальную БД
  python -m scripts.dev_test --local

  # Интерактивно через HTTP к запущенному серверу (как будет делать Яндекс)
  python -m scripts.dev_test --url http://127.0.0.1:8000/alice

  # Проверить конкретный переход: реплики через «|», первая — запуск навыка
  python -m scripts.dev_test --local --say "|да|подскажи|в саду"

  # Удалить тестового игрока (и его прогресс) из локальной БД
  python -m scripts.dev_test --reset --player dev-player

Пустая реплика = запуск навыка (новая сессия).
Это локальная имитация протокола. Реальную интеграцию проверяет только консоль Яндекс Диалогов.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from app.config import get_settings
from app.content import load_episode
from app.db.database import Database
from app.game.engine import Engine
from app.service import GameService


def build_payload(utterance: str, player: str, session_id: str, message_id: int, new: bool) -> dict:
    return {
        "meta": {"locale": "ru-RU", "timezone": "Europe/Moscow", "interfaces": {"screen": {}}},
        "request": {"type": "SimpleUtterance", "command": utterance, "original_utterance": utterance},
        "session": {
            "message_id": message_id,
            "session_id": session_id,
            "skill_id": "dev-skill",
            "user_id": player,
            "application": {"application_id": player},
            "new": new,
        },
        "version": "1.0",
    }


class LocalTransport:
    def __init__(self, player: str) -> None:
        settings = get_settings()
        self.service = GameService(
            self._db(settings.database_url), Engine(load_episode()), settings.player_key_pepper
        )
        self.player = player

    @staticmethod
    def _db(url: str) -> Database:
        db = Database(url)
        db.create_schema()
        return db

    def send(self, payload: dict) -> dict:
        s = payload["session"]
        out = self.service.handle_turn(
            player_id=self.player,
            session_id=s["session_id"],
            message_id=s["message_id"],
            utterance=payload["request"]["command"],
            new_session=s["new"],
        )
        return {"text": out.text, "buttons": out.buttons, "end_session": out.end_session}


class HttpTransport:
    def __init__(self, url: str) -> None:
        import httpx

        self.client = httpx.Client(timeout=5)
        self.url = url

    def send(self, payload: dict) -> dict:
        r = self.client.post(self.url, json=payload)
        r.raise_for_status()
        resp = r.json()["response"]
        return {
            "text": resp["text"],
            "buttons": [b["title"] for b in resp.get("buttons", [])],
            "end_session": resp["end_session"],
        }


def show(reply: dict) -> None:
    print(f"\nИскорка: {reply['text']}")
    if reply["buttons"]:
        print(f"  [кнопки: {' | '.join(reply['buttons'])}]")
    if reply["end_session"]:
        print("  [сессия завершена]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--local", action="store_true", help="работать через сервис и локальную БД")
    mode.add_argument("--url", help="адрес webhook, например http://127.0.0.1:8000/alice")
    mode.add_argument("--reset", action="store_true", help="удалить игрока из локальной БД")
    parser.add_argument("--player", default="dev-player", help="идентификатор тестового игрока")
    parser.add_argument("--say", help="реплики через |; первая пустая = запуск навыка")
    args = parser.parse_args()

    if args.reset:
        settings = get_settings()
        db = Database(settings.database_url)
        db.create_schema()
        service = GameService(db, Engine(load_episode()), settings.player_key_pepper)
        print("Удалён." if service.reset_player(args.player) else "Такого игрока нет.")
        return 0

    if not args.local and not args.url:
        parser.error("укажите --local или --url")
    transport = LocalTransport(args.player) if args.local else HttpTransport(args.url)

    session_id = uuid.uuid4().hex[:16]
    message_id = 0
    new = True

    def turn(text: str) -> bool:
        nonlocal message_id, new, session_id
        reply = transport.send(build_payload(text, args.player, session_id, message_id, new))
        print(f"Ты: {text!r}" if text else "Ты: (запуск навыка)")
        show(reply)
        message_id += 1
        new = False
        if reply["end_session"]:
            session_id = uuid.uuid4().hex[:16]  # следующий запуск — новая сессия
            message_id, new = 0, True
            return False
        return True

    if args.say is not None:
        for utterance in args.say.split("|"):
            turn(utterance.strip())
        return 0

    print("Пустая строка = запуск навыка. Ctrl+C — выход.")
    turn("")
    try:
        while True:
            text = input("\nТы: ").strip()
            turn(text)
    except (KeyboardInterrupt, EOFError):
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
