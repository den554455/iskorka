"""Миграция старой базы (версия с одним эпизодом) при старте."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.database import Database
from app.db.models import Decision


OLD_SCHEMA = """
CREATE TABLE profiles (
    id INTEGER NOT NULL PRIMARY KEY,
    player_key VARCHAR(64) NOT NULL UNIQUE,
    episode_id VARCHAR(32) NOT NULL,
    node VARCHAR(48) NOT NULL,
    unclear_count INTEGER NOT NULL DEFAULT 0,
    turn INTEGER NOT NULL DEFAULT 0,
    last_session_id VARCHAR(64),
    last_message_id INTEGER,
    last_response TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE decisions (
    id INTEGER NOT NULL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    episode_id VARCHAR(32) NOT NULL,
    key VARCHAR(48) NOT NULL,
    value VARCHAR(48) NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE (profile_id, episode_id, key)
);
INSERT INTO profiles (id, player_key, episode_id, node) VALUES (1, 'k1', 'ep1', 'EPISODE_COMPLETE');
INSERT INTO decisions VALUES (1, 1, 'ep1', 'seed_place', 'garden', '2026-10-20 10:00:00');
"""


@pytest.fixture
def old_db(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.commit()
    con.close()
    return path


def test_old_database_is_migrated_and_data_kept(old_db):
    db = Database(f"sqlite:///{old_db}")
    try:
        db.create_schema()
        con = sqlite3.connect(old_db)
        columns = {row[1] for row in con.execute("PRAGMA table_info(profiles)")}
        assert {"episodes_started", "session_new"} <= columns
        assert con.execute("SELECT player_key, node FROM profiles").fetchall() == [("k1", "EPISODE_COMPLETE")]
        assert con.execute("SELECT key, value FROM decisions").fetchall() == [("seed_place", "garden")]
        con.close()
    finally:
        db.dispose()


def test_migrated_decisions_are_unique_per_profile_and_key(old_db):
    db = Database(f"sqlite:///{old_db}")
    try:
        db.create_schema()
        with pytest.raises(IntegrityError):
            with db.session_scope() as s:
                s.add(Decision(profile_id=1, episode_id="ep2", key="seed_place", value="stream"))
    finally:
        db.dispose()


def test_migration_is_idempotent(old_db):
    for _ in range(2):
        db = Database(f"sqlite:///{old_db}")
        db.create_schema()
        db.dispose()
    con = sqlite3.connect(old_db)
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
    con.close()
