"""Подключение к SQLite и управление транзакциями."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


class Database:
    def __init__(self, url: str) -> None:
        parsed = make_url(url)
        if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
            Path(parsed.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

        self.engine: Engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 10},
        )

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_connection, _record):  # pragma: no cover - вызывается драйвером
            # Рецепт SQLAlchemy для pysqlite: драйвер сам управляет транзакциями и из-за этого
            # SAVEPOINT (begin_nested) преждевременно фиксирует внешнюю транзакцию. Отключаем
            # неявное управление и открываем BEGIN сами (см. ниже) — тогда rollback откатывает
            # и привязку профиля, и сам ход.
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        @event.listens_for(self.engine, "begin")
        def _begin(conn):  # pragma: no cover - вызывается SQLAlchemy
            conn.exec_driver_sql("BEGIN")

        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        self._migrate()

    def _migrate(self) -> None:
        """Простые миграции при старте для SQLite в /data (см. SPEC_STORY, раздел «Миграции БД»).

        1. profiles.episodes_started — мягкий ограничитель числа эпизодов за визит.
        2. decisions: UNIQUE(profile_id, episode_id, key) -> UNIQUE(profile_id, key),
           чтобы решение не перезаписывалось даже при повторном прохождении эпизода.
        3. profiles.user_key + уникальный индекс — привязка прогресса к session.user.user_id.
        """
        if self.engine.dialect.name != "sqlite":
            return
        with self.engine.begin() as conn:
            columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(profiles)")
            }
            if "episodes_started" not in columns:
                conn.exec_driver_sql(
                    "ALTER TABLE profiles ADD COLUMN episodes_started INTEGER NOT NULL DEFAULT 0"
                )
            if "session_new" not in columns:
                conn.exec_driver_sql(
                    "ALTER TABLE profiles ADD COLUMN session_new INTEGER NOT NULL DEFAULT 0"
                )
            if "user_key" not in columns:
                # 3. Привязка к session.user.user_id. SQLite не умеет ADD COLUMN ... UNIQUE,
                # поэтому уникальность задаёт отдельный индекс (NULL-ы допускаются много раз).
                conn.exec_driver_sql("ALTER TABLE profiles ADD COLUMN user_key VARCHAR(64)")
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_profiles_user_key ON profiles(user_key)"
            )

            indexes = {
                row[1] for row in conn.exec_driver_sql("PRAGMA index_list(decisions)")
            }
            needs_rebuild = any("episode_id" in (idx or "") for idx in indexes)
            if not needs_rebuild:
                # старые БД: проверка по списку колонок уникального ограничения
                ddl = conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'"
                ).scalar()
                needs_rebuild = ddl is not None and "episode_id" in ddl and "UNIQUE" in ddl.upper()
            if needs_rebuild:
                conn.exec_driver_sql(
                    """
                    CREATE TABLE decisions_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                        episode_id VARCHAR(32) NOT NULL,
                        key VARCHAR(48) NOT NULL,
                        value VARCHAR(48) NOT NULL,
                        created_at DATETIME NOT NULL,
                        UNIQUE (profile_id, key)
                    )
                    """
                )
                conn.exec_driver_sql(
                    """
                    INSERT INTO decisions_new (id, profile_id, episode_id, key, value, created_at)
                    SELECT id, profile_id, episode_id, key, value, created_at
                    FROM decisions ORDER BY id
                    """
                )
                conn.exec_driver_sql("DROP TABLE decisions")
                conn.exec_driver_sql("ALTER TABLE decisions_new RENAME TO decisions")

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Одна транзакция: commit при успехе, rollback при любой ошибке."""
        session = self._sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
