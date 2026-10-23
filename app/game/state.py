"""Структуры данных игры. Никакой зависимости от Алисы или БД."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GameContext:
    """Снимок состояния игрока, который читает движок."""

    episode_id: str = "ep1"
    node: str = "NEW_GAME"
    unclear_count: int = 0
    turn: int = 0
    decisions: dict[str, str] = field(default_factory=dict)  # ключ решения -> значение
    items: set[str] = field(default_factory=set)
    met: set[str] = field(default_factory=set)
    completed: bool = False
    episodes_started: int = 0  # сколько эпизодов начато в этом профиле (для мягкого ограничителя)


@dataclass
class TurnResult:
    """Результат одного хода: что сказать и что изменить."""

    text: str
    buttons: list[str]
    end_session: bool
    kind: str  # story | resume | help | about | hint | repeat | unclear | exit | danger | end | next_episode
    node: str  # узел, который нужно сохранить после хода
    unclear_count: int
    turn: int
    decision: tuple[str, str] | None = None  # (ключ, значение)
    unlocks: list[str] = field(default_factory=list)
    meets: list[str] = field(default_factory=list)
    completed: bool = False
    path: list[str] = field(default_factory=list)  # пройденные узлы (для тестов и отладки)
    next_episode_id: str | None = None  # если ход запускает следующий эпизод (смена профиля)
