"""Каждая кнопка каждого вопроса должна распознаваться как ровно одно ожидаемое намерение."""

from __future__ import annotations

import pytest

from app.content import load_data
from app.game import intents

HINT_BUTTONS = {"Подскажи", "Не знаю", "Повтори"}
HUB = "EPISODE_COMPLETE"


def _questions():
    for episode_id, ep in load_data()["episodes"].items():
        for name, node in ep["nodes"].items():
            if node.get("expects") and node.get("buttons") and name != HUB:
                yield episode_id, name, node


QUESTIONS = list(_questions())


@pytest.mark.parametrize("episode_id,name,node", QUESTIONS, ids=[f"{e}:{n}" for e, n, _ in QUESTIONS])
def test_every_button_is_one_expected_intent(episode_id, name, node):
    expects = list(node["expects"])
    for title in node["buttons"]:
        if title in HINT_BUTTONS:
            continue
        found = intents.classify(intents.normalize(title), expects)
        assert len(found) == 1, f"{episode_id}:{name}: кнопка {title!r} распознана как {found}, ждали одно из {expects}"


@pytest.mark.parametrize("episode_id,name,node", QUESTIONS, ids=[f"{e}:{n}" for e, n, _ in QUESTIONS])
def test_every_expected_intent_has_a_button(episode_id, name, node):
    """Для каждого варианта ответа есть кнопка: ребёнок может выбрать его нажатием."""
    if not [t for t in node["buttons"] if t not in HINT_BUTTONS]:
        pytest.skip("в узле кнопки только подсказки: ответ говорится словом (ep1 RIDDLE)")
    hit = set()
    for title in node["buttons"]:
        hit.update(intents.classify(intents.normalize(title), list(node["expects"])))
    missing = set(node["expects"]) - hit - {"no", "yes"}
    assert not missing, f"{episode_id}:{name}: нет кнопки для {sorted(missing)}"


@pytest.mark.parametrize(
    "phrase,wanted,expected",
    [
        ("давай колокольчик", ["lantern", "bell"], "bell"),
        ("я хочу фонарик", ["lantern", "bell"], "lantern"),
        ("пусть живёт в пещере", ["cave", "stream", "garden"], "cave"),
        ("у ручья", ["cave", "stream", "garden"], "stream"),
        ("пойдём налево", ["left", "right"], "left"),
        ("конечно направо", ["left", "right"], "right"),
        ("пускай будет сверчок", ["cricket", "wind"], "cricket"),
        ("по очереди", ["turn", "together", "swap"], "turn"),
        ("все вместе", ["turn", "together", "swap"], "together"),
        ("давайте поменяемся", ["turn", "together", "swap"], "swap"),
        ("что случилось", ["happened", "how"], "happened"),
        ("как дела", ["happened", "how"], "how"),
        ("потом", ["yes", "no", "later", "next"], "later"),
    ],
)
def test_free_phrases_are_recognized(phrase, wanted, expected):
    assert intents.classify(intents.normalize(phrase), wanted) == [expected]


def test_negated_choice_is_not_a_choice():
    assert intents.classify(intents.normalize("не колокольчик"), ["lantern", "bell"]) == []


def test_nonsense_matches_nothing():
    for word in ("ыыы", "динозавр", "квантовая гравитация"):
        assert intents.classify(intents.normalize(word), list(intents.KNOWN_INTENTS)) == []
