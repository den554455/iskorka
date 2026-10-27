"""Проверки месяца контента: все 30 эпизодов, реестр мира, распознавание кнопок, устойчивость."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.content import episode_ids, episode_order, load_episode, load_series, load_world
from app.db.models import Decision
from app.db.repository import Repository
from app.game import intents
from app.game.engine import GIVE_UP_AT, Engine
from app.service import GameService, make_player_key, strip_sound_tags
from tests.conftest import PEPPER, Player

ALL = sorted(episode_ids(), key=lambda e: int(e[2:]))
HINTS = {"Подскажи", "Не знаю", "Повтори"}


def _nodes(eid: str) -> dict:
    return load_episode(eid)["nodes"]


def _waiting(eid: str) -> list[str]:
    return [n for n, v in _nodes(eid).items() if v.get("expects")]


def _texts(value) -> list[str]:
    if isinstance(value, dict):
        return [t for k, v in value.items() if k != "by_decision" for t in _texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    return [value] if isinstance(value, str) else []


# --- состав месяца и реестр мира -----------------------------------------------------


def test_thirty_episodes_in_series_order():
    assert len(ALL) == 30
    assert episode_order() == ALL
    arcs = load_series()["arcs"]
    assert len(arcs) == 5 and all(len(a["episodes"]) == 6 for a in arcs)


@pytest.mark.parametrize("eid", ALL)
def test_episode_links_and_metadata(eid):
    ep = load_episode(eid)
    order = episode_order()
    i = order.index(eid)
    assert ep["prev"] == (order[i - 1] if i else None)
    assert ep["title"].strip()
    assert ep["start"] in ep["nodes"]
    for key in ("help", "about", "exit", "resume_prefix", "danger"):
        assert ep["texts"][key], key


def test_decision_values_match_world_registry():
    world = load_world()["decision_keys"]
    for eid in ALL:
        for name, node in _nodes(eid).items():
            decision = node.get("decision")
            if not decision:
                continue
            entry = world[decision["key"]]
            assert entry["episode"] == eid, f"{eid}:{name}"
            assert set(decision["values"].values()) == set(entry["values"]), f"{eid}:{name}"
            assert entry["default"] in entry["values"]
            assert set(decision["values"]) <= set(node["expects"])


def test_every_decision_value_has_tailored_text_in_back_references():
    """Обратная ссылка называет каждое значение выбора, а не только запасной вариант."""
    world = load_world()["decision_keys"]
    for eid in ALL:
        for name, node in _nodes(eid).items():
            key = node.get("by_decision")
            if not key:
                continue
            for field in ("say", "resume"):
                variants = node.get(field)
                if isinstance(variants, dict):
                    assert "_default" in variants, f"{eid}:{name}.{field}"
                    declared = set(world[key]["values"])
                    named = {k for k in variants if not k.startswith("_")}
                    assert named <= declared, f"{eid}:{name}.{field}: лишние значения {named - declared}"


def test_each_decision_key_set_once_in_whole_month():
    seen: dict[str, str] = {}
    for eid in ALL:
        for node in _nodes(eid).values():
            decision = node.get("decision")
            if decision:
                assert seen.setdefault(decision["key"], eid) == eid, decision["key"]


# --- кнопки и слова -------------------------------------------------------------------


@pytest.mark.parametrize("eid", ALL)
def test_each_button_selects_exactly_one_option(eid):
    for name in _waiting(eid):
        node = _nodes(eid)[name]
        expects = list(node["expects"])
        for title in node.get("buttons", []):
            if title in HINTS:
                continue
            found = intents.classify(intents.normalize(title), expects)
            targets = {node["expects"][i] for i in found}
            assert found and len(targets) == 1, f"{eid}:{name}: кнопка {title!r} -> {found}"


@pytest.mark.parametrize("eid", ALL)
def test_every_choice_option_has_a_button(eid):
    """Чтобы можно было выбрать каждую ветку нажатием."""
    for name in _waiting(eid):
        node = _nodes(eid)[name]
        if not node.get("decision"):
            continue
        reached = set()
        for title in node["buttons"]:
            reached.update(intents.classify(intents.normalize(title), list(node["expects"])))
        assert reached >= set(node["decision"]["values"]), f"{eid}:{name}"


@pytest.mark.parametrize(
    "phrase,intent",
    [
        ("давай колокольчик", "bell"), ("хочу фонарик", "lantern"), ("пойдём налево", "left"),
        ("направо", "right"), ("по очереди", "turn"), ("все вместе", "together"),
        ("как дела", "how"), ("что случилось", "happened"), ("потом", "later"),
        ("в пещере", "cave"), ("на лугу", "meadow"), ("на холм", "hill"),
        ("хочу ягоды", "berries"), ("цветочки", "flowers"), ("помощь", "help"),
        ("пусть будет мир", "peace"), ("с больших", "big"), ("с маленьких", "small"),
        ("к радуге", "rainbow"), ("к облаку", "cloud"),
    ],
)
def test_free_phrases_recognised(phrase, intent):
    assert intent in intents.classify(intents.normalize(phrase), [intent])


@pytest.mark.parametrize("phrase,intent", [("не колокольчик", "bell"), ("не налево", "left"), ("не хочу ягоды", "berries")])
def test_negated_word_is_not_a_choice(phrase, intent):
    assert intent not in intents.classify(intents.normalize(phrase), [intent])


def test_unrelated_words_do_not_trigger_choices():
    for word in ("динозавр", "квантовая гравитация", "мммм"):
        for intent in intents.WORD_INTENTS:
            assert intent not in intents.classify(intents.normalize(word), [intent])


def test_help_button_is_an_answer_not_the_help_screen():
    engine = Engine(load_episode("ep28"), "ep28")
    ctx = replace(engine.new_context(), node="MAIN_ASK")
    out = engine.step(ctx, "Помощь")
    assert out.kind == "story" and out.decision == ("fifth_branch", "help")


def test_sound_tags_are_never_spoken():
    assert strip_sound_tags("Ура! [звук: ветер] Дальше. [звук: эхо]") == "Ура! Дальше."
    assert strip_sound_tags("[звук: ручей] Привет") == "Привет"


# --- устойчивость каждого узла ---------------------------------------------------------


def _ctx(engine: Engine, node: str, **kw):
    return replace(engine.new_context(), node=node, **kw)


@pytest.mark.parametrize("eid", ALL)
def test_resume_in_every_waiting_node(eid):
    engine = Engine(load_episode(eid), eid)
    for name in _waiting(eid):
        out = engine.step(_ctx(engine, name), "", new_session=True)
        assert out.text.strip() and len(out.text) <= 1024, f"{eid}:{name}"
        assert not out.end_session, f"{eid}:{name}"


@pytest.mark.parametrize("eid", ALL)
def test_nonsense_never_traps_and_decides_nothing(eid):
    engine = Engine(load_episode(eid), eid)
    for name in _waiting(eid):
        if name == "EPISODE_COMPLETE":
            continue
        ctx = _ctx(engine, name)
        ended = None
        for _ in range(60):  # «продвижение» упрощённого вопроса ведёт к следующим узлам
            out = engine.step(ctx, "ыыы мммм")
            assert len(out.text) <= 1024, f"{eid}:{name}"
            assert out.decision is None, f"{eid}:{name}: выбор сделан без осмысленного ответа"
            if out.end_session:
                ended = out
                break
            ctx = replace(ctx, node=out.node, unclear_count=out.unclear_count, turn=out.turn)
        assert ended is not None, f"{eid}:{name}: диалог не закончился"


@pytest.mark.parametrize("eid", ALL)
def test_hint_request_is_not_a_failure(eid):
    engine = Engine(load_episode(eid), eid)
    for name in _waiting(eid):
        out = engine.step(_ctx(engine, name), "подскажи")
        assert out.unclear_count == 0 and out.text.strip(), f"{eid}:{name}"


@pytest.mark.parametrize("eid", ALL)
def test_all_texts_within_limits_and_without_gender_slips(eid):
    for name, node in _nodes(eid).items():
        for field in ("say", "ask", "resume", "reask", "hint"):
            for text in _texts(node.get(field)):
                assert 0 < len(text) <= 1024, f"{eid}:{name}.{field}"
                assert "{" not in text and "}" not in text, f"{eid}:{name}: незаполненный шаблон"
                assert not re.search(r"\bты (вернул|догадал|остал|услышал|попал|выбрал|помог)", text.lower())
        for title in node.get("buttons", []):
            assert 0 < len(title) <= 64


@pytest.mark.parametrize("eid", ALL)
def test_sound_tags_have_known_format(eid):
    for node in _nodes(eid).values():
        for field in ("say", "ask", "resume", "reask", "hint"):
            for text in _texts(node.get(field)):
                for tag in re.findall(r"\[[^\]]*\]", text):
                    assert re.fullmatch(r"\[звук: [а-яё ]+\]", tag), tag


# --- сервис: переход, лимит визита, повторы ------------------------------------------


@pytest.fixture
def svc(db, multi_engine) -> GameService:
    return GameService(db, multi_engine, PEPPER)


def _to_hub_ep1(p: Player) -> None:
    for phrase in ["", "да", "да", "дерево", "давай в садик"]:
        p.say(phrase)


def _profile(db, pid: str):
    with db.session_scope() as s:
        pr = Repository(s).find_profile(make_player_key(pid, PEPPER))
        return pr.episode_id, pr.node, pr.episodes_started


def test_answer_right_after_transition_is_not_swallowed(svc, db):
    p = Player(svc, "t1")
    _to_hub_ep1(p)
    p.say("ещё")
    p.say("да")
    p.say("в ладошках")
    assert _profile(db, "t1")[1] == "NAME_ASK"


def test_visit_limit_resets_in_next_visit(svc, db):
    p = Player(svc, "t2")
    _to_hub_ep1(p)
    p.say("ещё")  # ep2
    for phrase in ["да", "в листике", "искра"]:
        p.say(phrase)
    stop = p.say("ещё")  # лимит визита
    assert stop.end_session
    assert _profile(db, "t2")[0] == "ep2"
    p.new_session("visit-2")
    back = p.say("")
    assert not back.end_session
    go = p.say("ещё")
    assert not go.end_session
    assert _profile(db, "t2")[0] == "ep3"


def test_back_reference_differs_by_first_choice(db, multi_engine):
    texts = {}
    for place, phrase in (("garden", "в саду"), ("stream", "у ручья")):
        svc = GameService(db, multi_engine, PEPPER)
        p = Player(svc, f"br-{place}")
        for ph in ["", "да", "да", "дерево", phrase, "ещё", "да", "в листике", "искра"]:
            p.say(ph)
        p.say("ещё")  # лимит визита
        p.new_session("v2")
        p.say("")
        texts[place] = p.say("ещё").text
    assert texts["garden"] != texts["stream"]
    assert "сад" in texts["garden"].lower() and "ручь" in texts["stream"].lower()


def test_duplicate_transition_request_counts_once(svc, db):
    p = Player(svc, "t3")
    _to_hub_ep1(p)
    mid = p.message_id
    kwargs = dict(player_id="t3", session_id="s1", utterance="ещё", new_session=False)
    first = svc.handle_turn(message_id=mid, **kwargs)
    again = svc.handle_turn(message_id=mid, **kwargs)
    assert again.replayed and again.text == first.text
    assert _profile(db, "t3")[0] == "ep2" and _profile(db, "t3")[2] == 2


def test_completed_episode_hub_in_new_visit_reflects_choice(svc, db):
    p = Player(svc, "t4")
    _to_hub_ep1(p)
    p.new_session("v2")
    back = p.say("")
    assert "сад" in back.text.lower() and "выбрать сад или ручей" not in back.text.lower()


def test_no_sound_tags_and_no_double_recap_in_full_walk(db, multi_engine, monkeypatch):
    import tests.test_all_episodes as walk

    seen: list[str] = []
    real = GameService.handle_turn

    def spy(self, **kw):
        out = real(self, **kw)
        seen.append(out.text)
        return out

    monkeypatch.setattr(GameService, "handle_turn", spy)
    walk._walk(GameService(db, multi_engine, PEPPER), db, lambda c: c[0])
    assert len(seen) > 100
    assert not [t for t in seen if "[звук" in t]
    assert not [t for t in seen if t.lower().count("в прошлый раз") > 1]
    assert max(len(t) for t in seen) <= 1024


def test_choices_are_saved_for_the_whole_month(db, multi_engine):
    import tests.test_all_episodes as walk

    service = GameService(db, multi_engine, PEPPER)
    walk._walk(service, db, lambda c: c[0])
    with db.session_scope() as s:
        keys = {d.key for d in s.scalars(select(Decision))}
    expected = {n["decision"]["key"] for e in ALL for n in _nodes(e).values() if n.get("decision")}
    assert keys == expected
