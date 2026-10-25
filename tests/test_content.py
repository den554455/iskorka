"""Проверки сценария: ссылки, достижимость, завершение, циклы, длины."""

from __future__ import annotations

import copy

from app.content import load_data
from app.content.validation import MAX_BUTTON_TITLE, MAX_TEXT, validate_content, validate_episode


def test_shipped_content_is_valid():
    assert validate_content(load_data()) == []


def test_episode_has_reachable_completion(episode):
    assert any(n.get("complete") for n in episode["nodes"].values())


def test_every_transition_target_exists(episode):
    nodes = episode["nodes"]
    for name, node in nodes.items():
        for target in node.get("expects", {}).values():
            assert target in nodes, f"{name} -> {target}"
        if node.get("auto_next"):
            assert node["auto_next"] in nodes


def test_choice_nodes_cover_all_intents(episode):
    node = episode["nodes"]["CHOOSE_PLACE"]
    assert set(node["decision"]["values"]) == set(node["expects"]) == {"garden", "stream"}


def test_all_texts_and_buttons_fit_platform_limits(episode):
    for node in episode["nodes"].values():
        for field in ("say", "ask", "resume", "reask", "hint"):
            value = node.get(field)
            if isinstance(value, dict):
                value = [v for items in value.values() for v in items]
            for text in value or []:
                assert 0 < len(text) <= MAX_TEXT
        for title in node.get("buttons", []):
            assert 0 < len(title) <= MAX_BUTTON_TITLE


def test_key_lines_have_at_least_three_variants(episode):
    nodes = episode["nodes"]
    for name in ("INTRO", "SEED_FOUND", "CHOOSE_PLACE", "GARDEN_SCENE", "STREAM_SCENE", "FINALE"):
        assert len(nodes[name]["say"] if "say" in nodes[name] else nodes[name]["ask"]) >= 3


# --- негативные проверки: валидатор действительно ловит ошибки -----------------------


def _broken(episode, mutate):
    ep = copy.deepcopy(episode)
    mutate(ep)
    return validate_episode("ep1", ep)


def test_validator_detects_missing_target(episode):
    errors = _broken(episode, lambda ep: ep["nodes"]["CHOOSE_PLACE"]["expects"].update(garden="NOPE"))
    assert any("несуществующий" in e for e in errors)


def test_validator_detects_unreachable_node(episode):
    errors = _broken(episode, lambda ep: ep["nodes"].update(ORPHAN={"say": ["x"], "end_session": True}))
    assert any("недостижим" in e for e in errors)


def test_validator_detects_auto_next_cycle(episode):
    def mutate(ep):
        ep["nodes"]["FINALE"].update(auto_next="GARDEN_SCENE")

    errors = _broken(episode, mutate)
    assert any("цикл" in e for e in errors)


def test_validator_detects_missing_completion(episode):
    # Убираем все end_session-узлы и complete у реальных финалов (FINALE без expects).
    # Хаб EPISODE_COMPLETE остаётся complete+expects — по новому контракту такой узел
    # финалом не считается, и валидатор обязан это подтвердить.
    def mutate(ep):
        for node in ep["nodes"].values():
            node.pop("end_session", None)
            if node.get("complete") and not node.get("expects"):
                node["complete"] = False

    errors = _broken(episode, mutate)
    assert any("финал" in e for e in errors)


def test_validator_detects_dead_end(episode):
    errors = _broken(episode, lambda ep: ep["nodes"].update(DEAD={"say": ["x"]}))
    assert any("тупик" in e for e in errors)


def test_validator_detects_too_long_text(episode):
    errors = _broken(episode, lambda ep: ep["nodes"]["BYE"]["say"].append("а" * 1100))
    assert any("длиннее" in e for e in errors)


def test_validator_detects_waiting_node_without_hint(episode):
    errors = _broken(episode, lambda ep: ep["nodes"]["INTRO"].pop("hint"))
    assert any("hint" in e for e in errors)
