"""Схема БД. Личных данных нет: игрок идентифицируется только хэшем идентификатора платформы."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Profile(Base):
    """Игровой профиль и текущее состояние диалога."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # HMAC-SHA256 от идентификатора приложения Алисы. UNIQUE создаёт индекс.
    player_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    episode_id: Mapped[str] = mapped_column(String(32), nullable=False)
    node: Mapped[str] = mapped_column(String(48), nullable=False)
    unclear_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Для защиты от повторной обработки одного и того же запроса.
    last_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Наш собственный последний ответ (не речь ребёнка).
    last_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Decision(Base):
    """Значимое сюжетное решение. Первое решение не перезаписывается."""

    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("profile_id", "episode_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    episode_id: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(48), nullable=False)
    value: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorldItem(Base):
    """Открытый элемент мира (награда). Один элемент — одна запись на профиль."""

    __tablename__ = "world_items"
    __table_args__ = (UniqueConstraint("profile_id", "item_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    item_key: Mapped[str] = mapped_column(String(48), nullable=False)
    episode_id: Mapped[str] = mapped_column(String(32), nullable=False)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CharacterMeeting(Base):
    """Флаг «ребёнок познакомился с персонажем»."""

    __tablename__ = "character_meetings"
    __table_args__ = (UniqueConstraint("profile_id", "character_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    character_key: Mapped[str] = mapped_column(String(32), nullable=False)
    met_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EpisodeProgress(Base):
    """Прогресс серий. completed_at выставляется один раз."""

    __tablename__ = "episode_progress"
    __table_args__ = (UniqueConstraint("profile_id", "episode_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    episode_id: Mapped[str] = mapped_column(String(32), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
