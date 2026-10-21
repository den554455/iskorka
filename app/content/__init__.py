"""Загрузка сценария. Некорректный сценарий не даёт приложению запуститься."""

from __future__ import annotations

import json
from pathlib import Path

from app.content.validation import validate_content

CONTENT_PATH = Path(__file__).with_name("episodes.json")
DEFAULT_EPISODE = "ep1"


class ContentError(RuntimeError):
    pass


def load_data(path: Path | None = None) -> dict:
    with open(path or CONTENT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def load_episode(episode_id: str = DEFAULT_EPISODE, path: Path | None = None) -> dict:
    data = load_data(path)
    errors = validate_content(data)
    if errors:
        raise ContentError("Сценарий некорректен:\n" + "\n".join(errors))
    return data["episodes"][episode_id]
