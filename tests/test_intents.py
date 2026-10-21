from __future__ import annotations

import pytest

from app.game import intents
from app.game.intents import classify, normalize


@pytest.mark.parametrize(
    "phrase",
    ["В саду", "Давай в садик", "посадим в саду", "в сад", "в Садике!", "на клумбе"],
)
def test_garden_phrases(phrase):
    assert classify(normalize(phrase), ["garden", "stream"]) == ["garden"]


@pytest.mark.parametrize(
    "phrase",
    ["Посадим у ручья", "Возле воды", "у ручейка", "на берегу", "ручеёк", "около речки"],
)
def test_stream_phrases(phrase):
    assert classify(normalize(phrase), ["garden", "stream"]) == ["stream"]


def test_both_places_is_ambiguous():
    assert len(classify(normalize("в саду или у ручья"), ["garden", "stream"])) == 2


def test_negated_place_is_not_a_choice():
    assert classify(normalize("не в саду"), ["garden", "stream"]) == []
    assert classify(normalize("не в саду, а у ручья"), ["garden", "stream"]) == ["stream"]


def test_garbage_is_not_recognised():
    assert classify(normalize("бульдозер динозавр"), ["garden", "stream"]) == []
    assert classify(normalize(""), ["garden", "stream"]) == []
    assert classify(normalize(None), ["garden", "stream"]) == []


@pytest.mark.parametrize("phrase", ["да", "Давай!", "ну конечно", "ага", "хочу", "Поехали"])
def test_yes(phrase):
    assert classify(normalize(phrase), ["yes", "no"]) == ["yes"]


@pytest.mark.parametrize("phrase", ["нет", "не хочу", "Не сейчас", "неа"])
def test_no(phrase):
    assert classify(normalize(phrase), ["yes", "no"]) == ["no"]


@pytest.mark.parametrize("phrase", ["хватит", "Стоп!", "я хочу выйти", "пока", "до свидания"])
def test_exit(phrase):
    assert intents.is_exit(normalize(phrase))


def test_exit_not_triggered_by_normal_words():
    assert not intents.is_exit(normalize("в саду пока тихо"))
    assert not intents.is_exit(normalize("в саду"))


def test_help_about_hint_repeat():
    assert intents.is_help(normalize("Помощь"))
    assert intents.is_about(normalize("Что ты умеешь?"))
    assert intents.is_hint(normalize("подскажи"))
    assert intents.is_hint(normalize("не знаю"))
    assert intents.is_repeat(normalize("повтори"))
    assert intents.is_repeat(normalize("что?"))


def test_normalize_handles_yo_punctuation_and_length():
    assert normalize("Ручьё!!!") == "ручье"
    assert len(normalize("а " * 500)) <= intents.MAX_TEXT_CHARS
