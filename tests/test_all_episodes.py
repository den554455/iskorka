"""Сквозной проход всех 30 эпизодов только по кнопкам: проверяет, что каждая кнопка понимается.

Бот отвечает ровно тем, что написано на кнопках (как ребёнок, который нажимает их). Если
какое-то слово кнопки не распознаётся, диалог застрянет на узле, и тест это покажет.
"""

from __future__ import annotations

import pytest

from app.content import episode_order
from app.db.repository import Repository
from app.service import GameService, make_player_key
from tests.conftest import PEPPER, Player

HINT_BUTTONS = {"Подскажи", "Не знаю", "Повтори"}
# Загадка эпизода 1 принимает слово-ответ, а кнопки в ней только подсказки.
FREE_ANSWERS = {("ep1", "RIDDLE"): "дерево"}
MAX_TURNS = 800


def _state(db, player_id: str) -> tuple[str, str, dict]:
    with db.session_scope() as s:
        profile = Repository(s).find_profile(make_player_key(player_id, PEPPER))
        ctx = Repository(s).load_context(profile)
        return profile.episode_id, profile.node, dict(ctx.decisions)


def _walk(service: GameService, db, pick) -> tuple[list[str], dict]:
    player = Player(service, player_id="walker")
    out = player.say("")
    visited: list[str] = []
    visit = 0
    stuck = 0
    last = None
    for _ in range(MAX_TURNS):
        episode, node, decisions = _state(db, "walker")
        if not visited or visited[-1] != episode:
            visited.append(episode)
        if episode == "ep30" and node == "EPISODE_COMPLETE":
            return visited, decisions
        stuck = stuck + 1 if (episode, node) == last else 0
        last = (episode, node)
        assert stuck < 4, f"диалог застрял на {episode}:{node}; последний ответ: {out.text[:200]!r}"
        buttons = out.buttons
        if "Ещё" in buttons:
            reply = "Ещё"
        elif (episode, node) in FREE_ANSWERS:
            reply = FREE_ANSWERS[(episode, node)]
        else:
            choices = [b for b in buttons if b not in HINT_BUTTONS] or ["Да"]
            reply = "Да" if "Да" in choices else pick(choices)  # на вопрос «да/нет» ребёнок соглашается
        out = player.say(reply)
        if out.end_session:  # лимит визита: «на сегодня хватит» — возвращаемся в новом визите
            visit += 1
            player.new_session(f"s{visit}")
            out = player.say("")
    pytest.fail(f"за {MAX_TURNS} ходов дошли только до {visited[-1]}")


@pytest.mark.parametrize("name,pick", [("first", lambda c: c[0]), ("last", lambda c: c[-1])])
def test_all_30_episodes_playable_by_buttons(db, multi_engine, name, pick, monkeypatch):
    import app.service as service_module

    real_fit = service_module.fit_text

    def strict_fit(text, limit=1024):
        assert len(text) <= limit, f"ответ длиннее {limit} знаков и был бы обрезан: {text[:80]!r}"
        return real_fit(text, limit)

    monkeypatch.setattr(service_module, "fit_text", strict_fit)
    service = GameService(db, multi_engine, PEPPER)
    visited, decisions = _walk(service, db, pick)
    assert visited == episode_order()
    assert len(visited) == 30
    assert len(decisions) >= 20, "решения детей должны сохраняться по ходу месяца"
