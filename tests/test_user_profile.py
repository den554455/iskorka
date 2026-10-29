"""Привязка прогресса к session.user.user_id: перенос анонимного профиля, конфликты, гонки, откат."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.database import Database
from app.db.models import CharacterMeeting, Decision, EpisodeProgress, Profile, WorldItem
from app.db.repository import Repository
from app.game.engine import build_multi_engine
from app.service import GameService, make_player_key, make_user_key
from tests.conftest import PEPPER, alice_payload
from tests.test_alice_webhook import post

STORY = [("", True), ("да", False), ("да", False), ("дерево", False), ("в саду", False)]


@pytest.fixture(scope="module")
def multi():
    return build_multi_engine()


@pytest.fixture
def svc(db, multi):
    return GameService(db, multi, PEPPER)


def play(svc, *, app="app-1", user=None, session="s1", steps=STORY, start_mid=0):
    out = None
    for i, (u, new) in enumerate(steps):
        out = svc.handle_turn(
            player_id=app, user_id=user, session_id=session, message_id=start_mid + i,
            utterance=u, new_session=new,
        )
    return out


def profiles(db):
    with db.session_scope() as s:
        return list(s.scalars(select(Profile).order_by(Profile.id)))


def anon_key(app="app-1"):
    return make_player_key(app, PEPPER)


# --- анонимные игроки: прежнее поведение ---------------------------------------------

def test_new_anonymous_player_gets_profile_without_user_key(svc, db):
    play(svc, steps=STORY[:1])
    (p,) = profiles(db)
    assert p.player_key == anon_key() and p.user_key is None


def test_repeated_anonymous_visit_uses_same_profile(svc, db):
    play(svc, steps=STORY[:2])
    play(svc, session="s2", steps=[("", True)])
    assert len(profiles(db)) == 1


# --- первый вход авторизованного ---------------------------------------------------

def test_first_login_without_anonymous_profile_creates_user_profile(svc, db):
    play(svc, user="u-1", steps=STORY[:1])
    (p,) = profiles(db)
    assert p.user_key == make_user_key("u-1", PEPPER) and p.player_key == p.user_key
    assert p.player_key != anon_key()  # ключи анонимного и авторизованного не пересекаются


def test_user_without_application_id_is_supported(svc, db):
    svc.handle_turn(player_id=None, user_id="u-1", session_id="s", message_id=0, utterance="", new_session=True)
    assert len(profiles(db)) == 1


def test_no_identity_at_all_is_rejected(svc):
    with pytest.raises(ValueError):
        svc.handle_turn(player_id=None, user_id=None, session_id="s", message_id=0, utterance="", new_session=True)


# --- перенос анонимного профиля ------------------------------------------------------

def test_anonymous_progress_is_linked_and_fully_preserved(svc, db):
    play(svc, steps=STORY)  # анонимно дошли до конца эпизода 1 с решением и наградой
    with db.session_scope() as s:
        before = Repository(s).load_context(s.scalars(select(Profile)).one())
        pid = s.scalars(select(Profile.id)).one()
    play(svc, user="u-1", session="s2", steps=[("", True)], start_mid=0)  # первый вход «с аккаунтом»
    (p,) = profiles(db)  # профиль по-прежнему один
    assert p.id == pid and p.user_key == make_user_key("u-1", PEPPER)
    assert p.player_key == anon_key()  # анонимный ключ сохранён: ничего не удалено
    with db.session_scope() as s:
        after = Repository(s).load_context(s.get(Profile, pid))
    assert after.decisions == before.decisions == {"seed_place": "garden"}
    assert after.items == before.items and "garden_flower" in after.items
    assert after.met == before.met and "tisha" in after.met
    assert after.completed and before.completed
    assert after.episode_id == before.episode_id


def test_relogin_after_link_loads_same_profile_without_second_transfer(svc, db):
    play(svc, steps=STORY[:2])
    play(svc, user="u-1", session="s2", steps=[("", True)])
    with db.session_scope() as s:
        outcomes = [
            Repository(s).resolve_profile(
                anon_key=anon_key(), user_key=make_user_key("u-1", PEPPER), episode_id="ep1", start_node="NEW_GAME"
            )[1]
            for _ in range(2)
        ]
    assert outcomes == ["user_existing", "user_existing"]
    assert len(profiles(db)) == 1


def test_continue_from_another_device_with_same_user(svc, db):
    play(svc, app="phone", user="u-1", steps=STORY)
    out = play(svc, app="speaker", user="u-1", session="s9", steps=[("", True)])
    assert len(profiles(db)) == 1
    (p,) = profiles(db)
    assert p.episode_id == "ep1" and p.node == "EPISODE_COMPLETE"
    assert out.text  # продолжение с места остановки, а не новое приветствие серии


# --- конфликты -----------------------------------------------------------------------

def test_conflict_keeps_user_profile_and_leaves_anonymous_untouched(svc, db):
    play(svc, app="phone", user="u-1", steps=STORY)          # прогресс пользователя
    play(svc, app="old-speaker", steps=STORY[:2])             # другой анонимный прогресс на другом устройстве
    with db.session_scope() as s:
        anon_before = s.scalars(select(Profile).where(Profile.player_key == anon_key("old-speaker"))).one()
        snap = (anon_before.id, anon_before.node, anon_before.turn, anon_before.user_key)
    play(svc, app="old-speaker", user="u-1", session="s5", steps=[("", True)])
    ps = profiles(db)
    assert len(ps) == 2
    anon = next(p for p in ps if p.player_key == anon_key("old-speaker"))
    assert (anon.id, anon.node, anon.turn, anon.user_key) == snap  # не изменён и не удалён
    mine = next(p for p in ps if p.user_key == make_user_key("u-1", PEPPER))
    assert mine.node == "EPISODE_COMPLETE"                       # прогресс пользователя не перезаписан
    with db.session_scope() as s:
        outcome = Repository(s).resolve_profile(
            anon_key=anon_key("old-speaker"), user_key=make_user_key("u-1", PEPPER), episode_id="ep1", start_node="NEW_GAME"
        )[1]
    assert outcome == "user_existing_conflict"


def test_anonymous_profile_of_another_user_is_not_taken_over(svc, db):
    play(svc, app="shared", steps=STORY)
    play(svc, app="shared", user="alice", session="s2", steps=[("", True)])       # привязан к alice
    play(svc, app="shared", user="bob", session="s3", steps=[("", True)])         # другой человек на том же устройстве
    ps = profiles(db)
    assert len(ps) == 2
    a = next(p for p in ps if p.user_key == make_user_key("alice", PEPPER))
    b = next(p for p in ps if p.user_key == make_user_key("bob", PEPPER))
    assert a.id != b.id and a.player_key == anon_key("shared")
    assert b.node != "EPISODE_COMPLETE"  # прогресс alice к bob не перешёл


def test_two_users_never_share_profile(svc, db):
    play(svc, user="u-1", steps=STORY[:2])
    play(svc, user="u-2", session="s2", steps=STORY[:2])
    assert len({p.id for p in profiles(db)}) == 2


# --- устойчивость: гонки и откат ------------------------------------------------------

def test_parallel_first_logins_create_one_profile(svc, db):
    errors = []

    def go(i):
        try:
            svc.handle_turn(
                player_id="app-1", user_id="u-1", session_id=f"s{i}", message_id=0, utterance="", new_session=True
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    ps = profiles(db)
    assert len(ps) == 1 and ps[0].user_key == make_user_key("u-1", PEPPER)


def test_race_on_link_and_on_create_falls_back_to_existing_profile(db, monkeypatch):
    """Гонка между процессами: пока мы решали, другой запрос уже создал/привязал профиль."""
    ukey = make_user_key("u-1", PEPPER)
    with db.session_scope() as s:
        s.add(Profile(player_key=ukey, user_key=ukey, episode_id="ep1", node="NEW_GAME"))
    # 1) создание: find_profile_by_user «ничего не нашёл», INSERT упирается в UNIQUE
    calls = {"n": 0}
    real = Repository.find_profile_by_user

    def blind_once(self, key):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(self, key)

    monkeypatch.setattr(Repository, "find_profile_by_user", blind_once)
    with db.session_scope() as s:
        p, outcome = Repository(s).resolve_profile(anon_key=None, user_key=ukey, episode_id="ep1", start_node="NEW_GAME")
        assert outcome == "user_existing" and p.user_key == ukey
    # 2) привязка: анонимный профиль есть, но user_key уже занят другим профилем
    with db.session_scope() as s:
        s.add(Profile(player_key=anon_key("a"), episode_id="ep1", node="NEW_GAME"))
    calls["n"] = 0
    with db.session_scope() as s:
        p, outcome = Repository(s).resolve_profile(anon_key=anon_key("a"), user_key=ukey, episode_id="ep1", start_node="NEW_GAME")
        assert outcome == "user_existing" and p.player_key == ukey
    assert len(profiles(db)) == 2


def test_db_failure_during_transfer_rolls_back(svc, db, monkeypatch):
    play(svc, steps=STORY[:2])
    with db.session_scope() as s:
        snap = s.scalars(select(Profile)).one()
        snap = (snap.id, snap.node, snap.turn, snap.user_key)

    def boom(*a, **k):
        raise OperationalError("UPDATE", {}, Exception("disk I/O error"))

    monkeypatch.setattr(Repository, "apply_turn", boom)
    with pytest.raises(OperationalError):
        play(svc, user="u-1", session="s2", steps=[("", True)])
    monkeypatch.undo()
    (p,) = profiles(db)
    assert (p.id, p.node, p.turn, p.user_key) == snap  # привязка откатилась вместе с ходом
    # и повторная попытка работает
    play(svc, user="u-1", session="s3", steps=[("", True)])
    assert profiles(db)[0].user_key == make_user_key("u-1", PEPPER)


def test_unique_index_forbids_duplicate_user_key(db):
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            for i in range(2):
                s.add(Profile(player_key=f"k{i}", user_key="same", episode_id="ep1", node="NEW_GAME"))


# --- протокол: что считается авторизованным ---------------------------------------------

def test_webhook_uses_session_user_user_id_only(client):
    anon = alice_payload("", new=True)
    anon["session"]["user_id"] = "legacy-looks-like-user"       # устаревшее поле: НЕ идентичность пользователя
    post(client, anon)
    authed = alice_payload("", new=True, session_id="s-auth")
    authed["session"]["user"] = {"user_id": "REAL-USER-ID"}
    post(client, authed)
    db = client.app.state.db
    ps = profiles(db)
    assert len(ps) == 1  # анонимный профиль привязан при первом входе с аккаунтом
    assert ps[0].user_key == make_user_key("REAL-USER-ID", PEPPER)
    assert ps[0].player_key == make_player_key("app-1", PEPPER)


def test_webhook_empty_or_missing_user_is_anonymous(client):
    for user in (None, {}, {"user_id": ""}, {"user_id": "   "}):
        p = alice_payload("", new=True, application_id="app-x")
        if user is not None:
            p["session"]["user"] = user
        assert post(client, p).status_code == 200
    assert all(x.user_key is None for x in profiles(client.app.state.db))


def test_webhook_only_user_id_without_application(client):
    p = alice_payload("", new=True)
    del p["session"]["application"]
    del p["session"]["user_id"]
    p["session"]["user"] = {"user_id": "U-ONLY"}
    r = post(client, p)
    assert r.status_code == 200 and "Ой, что-то пошло не так" not in r.json()["response"]["text"]


# --- миграция БД ---------------------------------------------------------------------------

def test_migration_adds_user_key_without_losing_rows(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE profiles (
            id INTEGER NOT NULL PRIMARY KEY, player_key VARCHAR(64) NOT NULL UNIQUE,
            episode_id VARCHAR(32) NOT NULL, node VARCHAR(48) NOT NULL, unclear_count INTEGER NOT NULL,
            turn INTEGER NOT NULL, episodes_started INTEGER NOT NULL DEFAULT 0, session_new INTEGER NOT NULL DEFAULT 0,
            last_session_id VARCHAR(64), last_message_id INTEGER, last_response TEXT,
            created_at DATETIME, updated_at DATETIME);
        INSERT INTO profiles (id, player_key, episode_id, node, unclear_count, turn) VALUES (7,'old-key','ep3','DIRECTION_ASK',0,12);
        """
    )
    con.commit(); con.close()
    for _ in range(2):  # повторный запуск безопасен
        database = Database(f"sqlite:///{path}")
        database.create_schema()
        with database.engine.connect() as c:
            row = c.execute(text("SELECT id, player_key, episode_id, node, turn, user_key FROM profiles")).one()
            idx = {r[1] for r in c.exec_driver_sql("PRAGMA index_list(profiles)")}
        database.dispose()
        assert tuple(row) == (7, "old-key", "ep3", "DIRECTION_ASK", 12, None)
        assert "ux_profiles_user_key" in idx
