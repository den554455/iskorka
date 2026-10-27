"""Структурные проверки всех 30 эпизодов, реестра мира и порядка серий."""

from __future__ import annotations

import pytest

from app.content import (
    EPISODES_DIR,
    episode_ids,
    episode_order,
    load_data,
    load_series,
    load_world,
    next_episode_id,
)
from app.content.validation import validate_content, validate_world
from app.game import intents

DATA = load_data()
EPISODES = DATA["episodes"]
WORLD = load_world()
IDS = sorted(EPISODES, key=lambda e: int(e[2:]))


def test_thirty_episodes_in_order():
    assert IDS == [f"ep{i}" for i in range(1, 31)]
    assert episode_order() == IDS
    assert len(episode_ids()) == 30 and len(list(EPISODES_DIR.glob("ep*.json"))) == 30


def test_content_and_world_validate_without_errors():
    assert validate_content(DATA) == []
    assert validate_world(EPISODES, WORLD, load_series()) == []


def test_series_structure_five_arcs_fifteen_series():
    series = load_series()
    assert len(series["arcs"]) == 5
    assert [len(a["episodes"]) for a in series["arcs"]] == [6] * 5
    assert len(series["series"]) == 15
    paired = [e for s in series["series"] for e in s["episodes"]]
    assert paired == IDS and all(len(s["episodes"]) == 2 for s in series["series"])


@pytest.mark.parametrize("episode_id", IDS)
def test_prev_next_links_match_order(episode_id):
    i = IDS.index(episode_id)
    ep = EPISODES[episode_id]
    assert ep.get("prev") == (IDS[i - 1] if i else None)
    assert next_episode_id(episode_id) == (IDS[i + 1] if i < 29 else None)


@pytest.mark.parametrize("episode_id", IDS)
def test_every_episode_has_hub_and_finale(episode_id):
    nodes = EPISODES[episode_id]["nodes"]
    hub = nodes["EPISODE_COMPLETE"]
    assert hub.get("complete") and hub.get("by_decision")
    assert {"yes", "no"} <= set(hub["expects"])
    # «Ещё» ведёт в переход; у ep30 следующего эпизода нет, и движок мягко прощается (NO_NEXT_TEXT).
    assert hub["expects"].get("next") == "NEXT_EPISODE" and nodes["NEXT_EPISODE"].get("next_episode")
    assert (next_episode_id(episode_id) is None) == (episode_id == "ep30")


@pytest.mark.parametrize("episode_id", IDS)
def test_hub_resume_uses_declared_decision_with_default(episode_id):
    hub = EPISODES[episode_id]["nodes"]["EPISODE_COMPLETE"]
    key = hub["by_decision"]
    assert key in WORLD["decision_keys"], f"{key} не объявлен в world.json"
    assert "_default" in hub["resume"]
    declared = set(WORLD["decision_keys"][key]["values"])
    variants = {k for k in hub["resume"] if not k.startswith("_")}
    assert variants <= declared, f"{episode_id}: варианты {variants - declared} не объявлены в мире"


def test_world_registry_is_consistent():
    for key, spec in WORLD["decision_keys"].items():
        assert spec["default"] in spec["values"], key
        assert spec["episode"] in EPISODES, key
        owner = EPISODES[spec["episode"]]["nodes"]
        produced = {
            v for n in owner.values() if (n.get("decision") or {}).get("key") == key
            for v in n["decision"]["values"].values()
        }
        assert produced == set(spec["values"]), f"{key}: мир {spec['values']} ≠ эпизод {sorted(produced)}"


def test_every_decision_is_made_by_exactly_one_episode():
    owners: dict[str, set[str]] = {}
    for eid, ep in EPISODES.items():
        for node in ep["nodes"].values():
            if node.get("decision"):
                owners.setdefault(node["decision"]["key"], set()).add(eid)
    assert all(len(v) == 1 for v in owners.values()), owners
    assert set(owners) == set(WORLD["decision_keys"])


def test_back_references_point_to_earlier_episodes():
    """Эпизод ссылается только на решения, принятые раньше него."""
    made_in = {k: IDS.index(v["episode"]) for k, v in WORLD["decision_keys"].items()}
    for eid, ep in EPISODES.items():
        for name, node in ep["nodes"].items():
            key = node.get("by_decision")
            if key and key in made_in and name != "EPISODE_COMPLETE":
                assert made_in[key] <= IDS.index(eid), f"{eid}:{name} ссылается на будущий выбор {key}"


@pytest.mark.parametrize("episode_id", IDS)
def test_decision_values_are_reachable_through_expects(episode_id):
    for name, node in EPISODES[episode_id]["nodes"].items():
        decision = node.get("decision")
        if decision:
            assert set(decision["values"]) <= set(node["expects"]), f"{episode_id}:{name}"
            fb = node["fallback"]
            assert fb["type"] in {"binary", "advance"}


@pytest.mark.parametrize("episode_id", IDS)
def test_choice_nodes_have_binary_fallback_with_give_up(episode_id):
    for name, node in EPISODES[episode_id]["nodes"].items():
        if node.get("decision"):
            fb = node["fallback"]
            assert fb["type"] == "binary", f"{episode_id}:{name}"
            assert fb["yes"] in node["decision"]["values"] and fb["no"] in node["decision"]["values"]
            assert fb.get("give_up"), f"{episode_id}:{name}: нет текста мягкого прощания"


@pytest.mark.parametrize("episode_id", IDS)
def test_reward_and_branch_metadata(episode_id):
    ep = EPISODES[episode_id]
    for key in ("title", "series", "arc", "branch"):
        assert ep.get(key), f"{episode_id}: нет поля {key}"


def test_every_intent_used_is_known():
    for eid, ep in EPISODES.items():
        for name, node in ep["nodes"].items():
            for intent in node.get("expects", {}):
                assert intent in intents.KNOWN_INTENTS, f"{eid}:{name}: {intent}"


def test_no_text_has_unresolved_markers_or_double_spaces():
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)

    for eid, ep in EPISODES.items():
        for text in strings(ep):
            for bad in ("TODO", "XXX", "{{", "}}", "  "):
                assert bad not in text, f"{eid}: {bad!r} в {text[:60]!r}"
