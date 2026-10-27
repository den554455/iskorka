"""Тесты webhook на локальном TestClient. Это НЕ проверка реальной интеграции с Яндексом."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import PEPPER, alice_payload, mentions_both_places


def post(client, payload, **kw):
    return client.post("/alice", json=payload, **kw)


def test_valid_first_request_returns_greeting_in_protocol_format(client):
    r = post(client, alice_payload("", new=True))
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == "1.0"
    resp = data["response"]
    assert "Искорка" in resp["text"] and len(resp["text"]) <= 1024
    assert resp["end_session"] is False
    assert [b["title"] for b in resp["buttons"]] == ["Да", "Нет"]
    assert all(len(b["title"]) <= 64 and b["hide"] is True for b in resp["buttons"])


def test_full_dialogue_over_http(client):
    # После финала серия ждёт «ещё» или «нет»: «нет» мягко закрывает визит.
    steps = [("", True), ("да", False), ("да", False), ("дерево", False), ("в саду", False), ("нет", False)]
    last = None
    for i, (text, new) in enumerate(steps):
        last = post(client, alice_payload(text, message_id=i, new=new)).json()["response"]
    assert last["end_session"] is True
    assert "buttons" not in last
    # Повторный запуск в новой сессии
    back = post(client, alice_payload("", session_id="sess-2", new=True)).json()["response"]
    assert "саду" in back["text"] and back["end_session"] is False


def test_duplicate_request_over_http_is_idempotent(client):
    post(client, alice_payload("", message_id=0, new=True))
    post(client, alice_payload("да", message_id=1))
    post(client, alice_payload("да", message_id=2))
    first = post(client, alice_payload("сад", message_id=3)).json()
    again = post(client, alice_payload("сад", message_id=3)).json()
    assert first == again


def test_invalid_json_returns_400_without_details(client):
    r = client.post("/alice", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json() == {"error": "bad_request"}


def test_non_object_json_returns_400(client):
    assert post(client, [1, 2, 3]).status_code == 400


def test_missing_required_fields_return_400(client):
    assert post(client, {"version": "1.0"}).status_code == 400
    payload = alice_payload("да")
    del payload["session"]["session_id"]
    assert post(client, payload).status_code == 400
    payload = alice_payload("да")
    del payload["request"]
    assert post(client, payload).status_code == 400


def test_too_long_utterance_is_rejected(client):
    payload = alice_payload("a" * 1025)  # ASCII, чтобы тело осталось меньше лимита размера
    assert post(client, payload).status_code == 400


def test_oversized_body_is_rejected(client):
    payload = alice_payload("да")
    payload["padding"] = "x" * 5000
    assert post(client, payload).status_code == 413


def test_unexpected_fields_are_ignored(client):
    payload = alice_payload("", new=True)
    payload["something_new"] = {"a": 1}
    payload["request"]["nlu"] = {"tokens": [], "entities": [], "intents": {}}
    payload["state"] = {"session": {}, "user": {}, "application": {}}
    assert post(client, payload).status_code == 200


def test_missing_identity_gives_friendly_answer(client):
    payload = alice_payload("", new=True)
    payload["session"]["application"] = {}
    del payload["session"]["user_id"]
    r = post(client, payload)
    assert r.status_code == 200
    assert r.json()["response"]["end_session"] is True


def test_legacy_user_id_used_when_application_missing(client):
    payload = alice_payload("", new=True)
    del payload["session"]["application"]
    assert post(client, payload).status_code == 200


def test_unknown_command_is_handled_softly(client):
    post(client, alice_payload("", message_id=0, new=True))
    post(client, alice_payload("да", message_id=1))
    post(client, alice_payload("да", message_id=2))
    post(client, alice_payload("дерево", message_id=3))  # отгадали загадку -> вопрос про место
    r = post(client, alice_payload("квантовая гравитация", message_id=4)).json()["response"]
    assert r["end_session"] is False
    assert mentions_both_places(r["text"])


def test_help_and_what_can_you_do(client):
    post(client, alice_payload("", message_id=0, new=True))
    for i, phrase in enumerate(["Помощь", "Что ты умеешь"], start=1):
        r = post(client, alice_payload(phrase, message_id=i)).json()["response"]
        assert r["end_session"] is False and len(r["text"]) > 80
        assert "Да" in [b["title"] for b in r["buttons"]]


def test_help_works_as_first_message_of_session(client):
    r = post(client, alice_payload("помощь", new=True)).json()["response"]
    assert r["end_session"] is False and "Искорка" in r["text"]


def test_exit_command_ends_session(client):
    post(client, alice_payload("", message_id=0, new=True))
    r = post(client, alice_payload("хватит", message_id=1)).json()["response"]
    assert r["end_session"] is True
    # и прогресс сохранён
    back = post(client, alice_payload("", session_id="s2", new=True)).json()["response"]
    assert back["end_session"] is False


def test_ping_check_request_is_answered_without_touching_state(client):
    r = post(client, alice_payload("ping", new=True))
    assert r.status_code == 200 and r.json()["response"]["end_session"] is False
    first = post(client, alice_payload("", session_id="real", new=True)).json()["response"]["text"]
    assert "Искорка" in first  # профиль создаётся только настоящим запросом


def test_dangerous_context_gets_safe_answer(client):
    post(client, alice_payload("", message_id=0, new=True))
    r = post(client, alice_payload("плохое", message_id=1, dangerous=True)).json()["response"]
    assert "взрослому" in r["text"]


def test_engine_failure_does_not_leak_internal_details(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'e.db'}", player_key_pepper=PEPPER)
    app = create_app(settings)

    def boom(*a, **k):
        raise RuntimeError("SECRET-TRACE /home/app/engine.py line 42")

    monkeypatch.setattr(app.state.service.engine, "step", boom)
    with TestClient(app) as c:
        r = c.post("/alice", json=alice_payload("", new=True))
    assert r.status_code == 200
    body = r.text
    assert "SECRET-TRACE" not in body and "Traceback" not in body and "engine.py" not in body
    resp = r.json()["response"]
    assert resp["text"] and resp["end_session"] is False


def test_webhook_secret_path(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 's.db'}", player_key_pepper=PEPPER, webhook_secret="abc123"
    )
    with TestClient(create_app(settings)) as c:
        assert c.post("/alice", json=alice_payload("", new=True)).status_code == 404
        assert c.post("/alice/abc123", json=alice_payload("", new=True)).status_code == 200


def test_health_endpoint(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_logs_do_not_contain_speech_or_ids(client, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="iskorka"):
        post(client, alice_payload("", new=True, application_id="APP-ID-XYZ"))
        post(client, alice_payload("уникальнаяфраза", message_id=1, application_id="APP-ID-XYZ"))
    assert "APP-ID-XYZ" not in caplog.text and "уникальнаяфраза" not in caplog.text


def test_response_is_valid_json_text_within_limit(client):
    r = post(client, alice_payload("", new=True))
    parsed = json.loads(r.text)
    assert set(parsed) == {"response", "version"}


def test_sound_tags_never_reach_alice(client):
    """Пометки «[звук: ...]» из сценария убираются: без звуковых файлов Алиса прочла бы их вслух."""
    texts = []
    for i, (phrase, new) in enumerate([("", True), ("да", False), ("да", False), ("дерево", False)]):
        texts.append(post(client, alice_payload(phrase, message_id=i, new=new)).json()["response"]["text"])
    assert texts and not any("[звук" in t or "звук:" in t for t in texts)
