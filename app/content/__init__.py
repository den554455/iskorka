"""Загрузка сценария. Некорректный сценарий не даёт приложению запуститься.

Контент лежит в файлах: каждый эпизод — отдельный JSON в `episodes/`,
реестр мира — `world.json`, порядок серий и арок — `series.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.content.validation import validate_content, validate_world

CONTENT_DIR = Path(__file__).parent
EPISODES_DIR = CONTENT_DIR / "episodes"
WORLD_PATH = CONTENT_DIR / "world.json"
SERIES_PATH = CONTENT_DIR / "series.json"
DEFAULT_EPISODE = "ep1"


class ContentError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_episode_file(episode_id: str) -> dict:
    """Читает один эпизод из episodes/<id>.json без валидации."""
    return _read_json(EPISODES_DIR / f"{episode_id}.json")


def load_world() -> dict:
    return _read_json(WORLD_PATH)


def load_series() -> dict:
    return _read_json(SERIES_PATH)


def episode_ids() -> list[str]:
    """Номера эпизодов в порядке их файлов (ep01 … ep30)."""
    return sorted(p.stem for p in EPISODES_DIR.glob("ep*.json"))


def load_data(path: Path | None = None) -> dict:
    """Собирает старый единый формат {version, episodes:{...}} из файлов контента.

    Нужен для совместимости: валидатор и тесты работают с тем же объектом, что и раньше.
    """
    if path is not None:
        return _read_json(path)
    episodes = {eid: load_episode_file(eid) for eid in episode_ids()}
    return {"version": 1, "episodes": episodes}


def episode_order(series: dict | None = None) -> list[str]:
    """Порядок эпизодов месяца по аркам из series.json."""
    series = series or load_series()
    order: list[str] = []
    for arc in series.get("arcs", []):
        for eid in arc.get("episodes", []):
            if eid not in order:
                order.append(eid)
    return order


def next_episode_id(episode_id: str, series: dict | None = None) -> str | None:
    """Следующий эпизод по series.json; None, если его файл ещё не написан."""
    order = episode_order(series)
    if episode_id not in order:
        return None
    index = order.index(episode_id)
    if index + 1 >= len(order):
        return None
    candidate = order[index + 1]
    return candidate if (EPISODES_DIR / f"{candidate}.json").exists() else None


def load_episode(episode_id: str = DEFAULT_EPISODE, path: Path | None = None) -> dict:
    data = load_data(path)
    errors = validate_content(data)
    if path is None:
        errors = errors + validate_world(data["episodes"], load_world(), load_series())
    if errors:
        raise ContentError("Сценарий некорректен:\n" + "\n".join(errors))
    return data["episodes"][episode_id]
