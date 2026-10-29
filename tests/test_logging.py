"""Логирование: формат, request id, traceback, маскирование секретов, отсутствие дублей."""

from __future__ import annotations

import logging
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.logging_setup import MASK, _AccessLogMasker, configure_logging, parse_level, safe_path
from app.main import create_app
from app.service import GameService
from tests.conftest import PEPPER, alice_payload

SECRET = "S3CR3T-WEBHOOK-TOKEN"
CHILD_SPEECH = "мой-секретный-детский-ответ"
USER_ID = "REAL-YANDEX-USER-ID-12345"
APP_ID = "REAL-APP-ID-98765"


def make_client(tmp_path, *, secret="", raise_exc=True):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'log.db'}", player_key_pepper=PEPPER, webhook_secret=secret,
    )
    return TestClient(create_app(settings), raise_server_exceptions=raise_exc)


def iskorka_records(caplog):
    return [r for r in caplog.records if r.name == "iskorka"]


def payload(utterance="", **kw):
    p = alice_payload(utterance, application_id=APP_ID, **kw)
    p["session"]["user"] = {"user_id": USER_ID}
    return p


def all_log_text(caplog) -> str:
    out = []
    for r in caplog.records:
        if r.name.startswith("httpx"):  # это журнал тестового HTTP-клиента, а не приложения
            continue
        out.append(r.getMessage())
        if r.exc_info:
            out.append("".join(logging.Formatter().formatException(r.exc_info)))
    return "\n".join(out)


# --- успешный запрос, формат, request id ------------------------------------------------

def test_successful_request_is_logged_with_status_time_and_request_id(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    with make_client(tmp_path) as c:
        r = c.post("/alice", json=payload("", new=True))
    rid = r.headers["x-request-id"]
    line = next(x for x in iskorka_records(caplog) if "POST /alice ->" in x.getMessage())
    assert re.search(r"-> 200 \(\d+\.\d мс\)", line.getMessage())
    assert line.request_id == rid and re.fullmatch(r"[0-9a-f]{12}", rid)
    # все записи этого запроса несут один и тот же id (например, запись о созданном профиле)
    same = [x for x in iskorka_records(caplog) if getattr(x, "request_id", None) == rid]
    assert len(same) >= 2


def test_startup_is_logged(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    with make_client(tmp_path):
        pass
    assert any("Приложение запущено" in r.getMessage() for r in iskorka_records(caplog))


def test_output_format_on_stdout(tmp_path, capsys):
    with make_client(tmp_path) as c:
        c.get("/health")
    out = capsys.readouterr().out
    assert re.search(
        r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO iskorka \[[0-9a-f]{12}\] GET /health -> 200 \(\d+\.\d мс\)$",
        out, re.M,
    ), out


def test_incoming_request_id_is_reused_only_if_safe(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get("/health", headers={"X-Request-Id": "trace-42"}).headers["x-request-id"] == "trace-42"
        bad = c.get("/health", headers={"X-Request-Id": "bad id\twith spaces;<script>"})
        assert bad.headers["x-request-id"] != "bad id\twith spaces;<script>"
        assert re.fullmatch(r"[0-9a-f]{12}", bad.headers["x-request-id"])


# --- контролируемая ошибка: ровно одна запись с traceback ---------------------------------------

def test_handled_error_is_logged_once_with_traceback(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO)

    def boom(self, **kw):
        raise RuntimeError("сломался сценарий")

    monkeypatch.setattr(GameService, "handle_turn", boom)
    with make_client(tmp_path) as c:
        r = c.post("/alice", json=payload("", new=True))
    assert r.status_code == 200 and "запутались лапки" in r.json()["response"]["text"]  # ребёнок слышит мягкую фразу
    with_tb = [x for x in caplog.records if x.exc_info]
    assert len(with_tb) == 1  # одно исключение — одна запись
    assert "RuntimeError" in "".join(logging.Formatter().formatException(with_tb[0].exc_info))
    assert with_tb[0].levelno == logging.ERROR and "сценарий" in with_tb[0].getMessage()
    assert with_tb[0].request_id == r.headers["x-request-id"]


def test_db_error_is_logged_as_db_error_once(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO)

    def boom(self, **kw):
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr(GameService, "handle_turn", boom)
    with make_client(tmp_path) as c:
        c.post("/alice", json=payload("", new=True))
    errs = [x for x in caplog.records if x.levelno >= logging.ERROR]
    assert len(errs) == 1 and "Ошибка БД" in errs[0].getMessage() and errs[0].exc_info


# --- необработанное исключение: middleware, один traceback -----------------------------------------------

def test_unhandled_exception_logged_once_with_traceback_and_500(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    c = make_client(tmp_path, raise_exc=False)
    c.app.add_api_route("/boom", lambda: 1 / 0)
    with c:
        r = c.get("/boom")
    assert r.status_code == 500 and r.json()["request_id"] == r.headers["x-request-id"]
    errs = [x for x in caplog.records if x.levelno >= logging.ERROR]
    assert len(errs) == 1 and errs[0].exc_info and "ZeroDivisionError" in "".join(
        logging.Formatter().formatException(errs[0].exc_info)
    )
    assert "Необработанное исключение" in errs[0].getMessage()


# --- безопасность: секреты и данные ребёнка --------------------------------------------------------------------

def test_secret_path_and_query_are_masked(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    with make_client(tmp_path, secret=SECRET) as c:
        c.post(f"/alice/{SECRET}", json=payload("", new=True))
        c.post("/alice/WRONG-GUESS-123", json=payload("", new=True))        # 404: попытка подбора тоже не пишется
        c.get(f"/health?token=abc123&x=1&api_key=KEYVAL")
    text = all_log_text(caplog)
    assert SECRET not in text and "WRONG-GUESS-123" not in text and "abc123" not in text and "KEYVAL" not in text
    assert f"/alice/{MASK}" in text and "x=1" in text


def test_no_user_ids_or_child_speech_in_logs_even_on_errors(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    with make_client(tmp_path) as c:
        c.post("/alice", json=payload("", new=True))
        c.post("/alice", json=payload(CHILD_SPEECH, message_id=1))          # нераспознанный ответ
        c.post("/alice", content=b'{"broken": "' + CHILD_SPEECH.encode() + b'"', headers={"content-type": "application/json"})
        monkeypatch.setattr(GameService, "handle_turn", lambda self, **kw: (_ for _ in ()).throw(RuntimeError("x")))
        c.post("/alice", json=payload(CHILD_SPEECH, message_id=2))
    text = all_log_text(caplog)
    for forbidden in (CHILD_SPEECH, USER_ID, APP_ID, PEPPER):
        assert forbidden not in text, forbidden


def test_unrecognized_answer_is_logged_without_speech(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    with make_client(tmp_path) as c:
        c.post("/alice", json=payload("", new=True))
        c.post("/alice", json=payload("да", message_id=1))
        c.post("/alice", json=payload(CHILD_SPEECH, message_id=2))
    assert any("Нераспознанный ответ" in x.getMessage() for x in iskorka_records(caplog))


def test_profile_events_are_logged_without_platform_ids(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    with make_client(tmp_path) as c:
        anon = alice_payload("", new=True, application_id=APP_ID)
        c.post("/alice", json=anon)
        c.post("/alice", json=payload("", new=True, session_id="s2"))        # первый вход с аккаунтом → привязка
    msgs = [x.getMessage() for x in iskorka_records(caplog)]
    assert any("привязан к пользователю" in m for m in msgs)
    assert not any(USER_ID in m or APP_ID in m for m in msgs)


# --- уровень логирования --------------------------------------------------------------------------------------------

def test_invalid_level_does_not_silence_logs(caplog):
    assert parse_level("verbose")[0] == "INFO" and "verbose" in parse_level("verbose")[1]
    assert parse_level("debug") == ("DEBUG", None) and parse_level(None)[0] == "INFO"
    caplog.set_level(logging.WARNING)
    assert configure_logging("bogus") == "INFO"
    assert logging.getLogger().level == logging.INFO
    assert any("LOG_LEVEL" in r.getMessage() for r in caplog.records)


def test_configure_logging_is_idempotent():
    for _ in range(3):
        configure_logging("INFO")
    marked = [h for h in logging.getLogger().handlers if getattr(h, "_iskorka_handler", False)]
    assert len(marked) == 1


def test_uvicorn_access_records_are_masked():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "POST", f"/alice/{SECRET}?token=zzz", "1.1", 200), None)
    assert _AccessLogMasker(SECRET).filter(rec)
    msg = rec.getMessage()
    assert SECRET not in msg and "zzz" not in msg and "/alice/***" in msg and msg.endswith("200")


def test_safe_path_basics():
    assert safe_path("/health") == "/health"
    assert safe_path("/alice") == "/alice"
    assert safe_path("/alice/abc", "abc") == "/alice/***"
    assert safe_path("/x/abc/y", "abc") == "/x/***/y"
    assert safe_path("/h?password=1&a=b") == "/h?password=***&a=b"
