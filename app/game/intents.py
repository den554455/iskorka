"""Распознавание намерений по тексту. Правила заданы словарями, без ИИ.

Текст сначала нормализуется (регистр, ё, знаки препинания), затем сверяется со списками
слов и фраз. Распознавание намеренно щедрое: «в садик», «давай в сад», «возле воды».
"""

from __future__ import annotations

import re

MAX_TEXT_CHARS = 200

_NON_WORD = re.compile(r"[^0-9a-zа-я]+")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    cleaned = _NON_WORD.sub(" ", text.lower().replace("ё", "е"))
    return " ".join(cleaned.split())[:MAX_TEXT_CHARS]


def _phrase_in(text: str, phrase: str) -> bool:
    return f" {phrase} " in f" {text} "


def _any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_phrase_in(text, p) for p in phrases)


# --- Глобальные команды ------------------------------------------------------

EXIT_PHRASES = (
    "хватит", "стоп", "выход", "выйти", "выйди", "выключи", "закончить", "закончи",
    "заверши", "завершить", "до свидания", "до встречи", "не хочу играть", "надоело",
    "пока пока", "хочу выйти", "хочу закончить", "давай закончим", "достаточно",
)
EXIT_EXACT = ("пока",)

HINT_PHRASES = (
    "подскажи", "подсказка", "подсказку", "дай подсказку", "помоги выбрать", "не знаю",
    "что выбрать", "что выбирать", "затрудняюсь", "какие варианты", "варианты",
    "что можно выбрать",
)

ABOUT_PHRASES = (
    "что ты умеешь", "что умеешь", "что ты можешь", "что можешь", "кто ты", "ты кто",
    "расскажи о себе",
)

HELP_PHRASES = (
    "помощь", "помоги", "помоги мне", "что делать", "что мне делать", "как играть",
    "правила", "не понимаю",
)

REPEAT_PHRASES = (
    "повтори", "повторить", "еще раз", "не расслышал", "не расслышала", "не понял",
    "не поняла", "что ты сказал", "что сказал",
)
REPEAT_EXACT = ("что", "чего", "как")

# --- Ответы на вопросы -------------------------------------------------------

YES_WORDS = frozenset(
    "да ага угу давай давайте конечно хорошо ладно хочу поехали начнем начинай помогу "
    "помогаю поможем готов готова окей ок оке погнали пойдем идем".split()
)
NO_PHRASES = (
    "нет", "неа", "не хочу", "не буду", "не надо", "не сейчас", "позже", "в другой раз",
)

REPLAY_PHRASES = (
    "расскажи", "расскажи историю", "послушать", "хочу послушать", "как росло семечко",
    "что выросло", "хочу узнать",
)

GARDEN_WORDS = frozenset(
    "сад саду сада садик садику садика садике садом садах садики клумба клумбе клумбу "
    "клумбы огород огороде огорода".split()
)
STREAM_WORDS = frozenset(
    "ручей ручья ручью ручье ручьем ручейка ручеек ручейке ручейку ручейком вода воды воде "
    "воду водой водичка водички водичке водичку берег берегу берега берегом речка речке "
    "речку речки река реке реку".split()
)

PLACE_INTENTS = {"garden": GARDEN_WORDS, "stream": STREAM_WORDS}

KNOWN_INTENTS = frozenset({"yes", "no", "garden", "stream", "replay"})


def is_exit(text: str) -> bool:
    return text in EXIT_EXACT or _any_phrase(text, EXIT_PHRASES)


def is_hint(text: str) -> bool:
    return _any_phrase(text, HINT_PHRASES)


def is_about(text: str) -> bool:
    return _any_phrase(text, ABOUT_PHRASES)


def is_help(text: str) -> bool:
    return _any_phrase(text, HELP_PHRASES)


def is_repeat(text: str) -> bool:
    return text in REPEAT_EXACT or _any_phrase(text, REPEAT_PHRASES)


def _yes_no(text: str) -> str | None:
    if _any_phrase(text, NO_PHRASES):
        return "no"
    if any(token in YES_WORDS for token in text.split()):
        return "yes"
    return None


def _places(text: str) -> set[str]:
    """Места, названные без отрицания. «Не в саду» не считается выбором сада."""
    tokens = text.split()
    found: set[str] = set()
    for index, token in enumerate(tokens):
        for intent, words in PLACE_INTENTS.items():
            if token in words and "не" not in tokens[max(0, index - 2):index]:
                found.add(intent)
    return found


def classify(text: str, wanted: list[str]) -> list[str]:
    """Возвращает список подошедших намерений из `wanted`.

    Пустой список — ничего не распознано; больше одного — ответ неоднозначен.
    """
    found: list[str] = []
    if "yes" in wanted or "no" in wanted:
        answer = _yes_no(text)
        if answer in wanted:
            found.append(answer)
    places = _places(text)
    for intent in PLACE_INTENTS:
        if intent in wanted and intent in places:
            found.append(intent)
    if "replay" in wanted and _any_phrase(text, REPLAY_PHRASES):
        found.append("replay")
    return found
