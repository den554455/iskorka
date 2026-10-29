"""Ударения: безопасное применение `_STRESS`. Верность ударений на слух здесь НЕ проверяется."""

from __future__ import annotations

import json
import re

import pytest

from app.audio import _STRESS, SKILL_ID, apply_stress, build_tts, speech_markup_stripped

# (вход, ожидаемый выход) для каждого правила и его проблемных словоформ
CASES = [
    ("У неё четыре колеса.", "У неё четыре колес+а."),
    ("Два колеса и три колеса", "Два колес+а и три колес+а"),
    ("Обе звезды красивые.", "Обе звезд+ы красивые."),
    ("Две звезды: одна высоко.", "Две звезд+ы: одна высоко."),
    ("Давайте разомнём головы!", "Давайте разомнём голов+ы!"),
    ("руки-ветки и ноги-корни", "руки-ветки и ног+и-корни"),
]


@pytest.mark.parametrize("src,expected", CASES)
def test_each_rule_and_word_form(src, expected):
    assert apply_stress(src) == expected


def test_every_rule_is_covered_by_a_case():
    for rx, _ in _STRESS:
        assert any(rx.search(src) for src, _ in CASES), rx.pattern


@pytest.mark.parametrize("src", [
    "колёса катятся", "много колес", "колесами", "подколеса", "перед колеса",       # не та форма / внутри другого слова
    "звёзды светят", "звездочёт", "обезьяны", "ноги отдыхают", "головы",              # без нужного контекста
    "Искорка, Тиша, Пуговка и Кап", "ветряной фонарь", "Привет! Я Искорка.",          # имена и обычные слова
])
def test_ordinary_words_and_other_forms_are_untouched(src):
    assert apply_stress(src) == src


def test_no_partial_replacement_inside_other_words():
    assert apply_stress("пять колесами") == "пять колесами"
    assert apply_stress("четыре колесаа") == "четыре колесаа"
    assert apply_stress("безноги-корни") == "безноги-корни"


def test_case_of_surrounding_words_is_preserved():
    assert apply_stress("Четыре колеса") == "Четыре колес+а"
    assert apply_stress("ОБЕ ЗВЕЗДЫ") == "ОБЕ ЗВЕЗДЫ"  # правила не угадывают регистр заглавных слов


def test_second_pass_changes_nothing():
    once = apply_stress("Четыре колеса, две звезды и разомнём головы, ноги-корни")
    assert apply_stress(once) == once
    assert build_tts(once, 1024) == once  # и через весь конвейер озвучки


def test_markers_tags_and_pauses_are_not_touched():
    tag = f"<speaker audio='dialogs-upload/{SKILL_ID}/971043da-1528-4298-afad-2bb52bd08711.opus'>"
    text = f"{tag} sil<<300>> четыре колеса [звук: ветер] {tag}"
    out = apply_stress(text)
    assert out.count(tag) == 2 and "sil<<300>>" in out and "[звук: ветер]" in out and "колес+а" in out


def test_tts_keeps_audio_tags_and_text_equals_stripped_tts():
    raw = "[звук: дуб] У неё четыре колеса. [пауза] Обе звезды — красивые?"
    tts = build_tts(raw, 1024)
    assert re.search(r"<speaker audio='dialogs-upload/[0-9a-f-]{36}/971043da-1528-4298-afad-2bb52bd08711\.opus'>", tts)
    assert "колес+а" in tts and "звезд+ы" in tts
    assert speech_markup_stripped(tts) == "У неё четыре колеса. Обе звезды — красивые?"


def test_stress_not_in_displayed_text_json_or_saved_response():
    """`+` попадает только в tts; сохранённый в БД ответ и text остаются исходными словами."""
    from app.service import strip_sound_tags

    assert strip_sound_tags("четыре колеса") == "четыре колеса"
    body = json.dumps({"tts": build_tts("четыре колеса", 1024)}, ensure_ascii=False)
    assert json.loads(body)["tts"] == "четыре колес+а"  # валидный JSON с «+»


def test_names_are_deliberately_not_in_dictionary():
    """Имена героев в `_STRESS` не добавлены: нет подтверждения прослушиванием (см. STRESS_CANDIDATES.md)."""
    for name in ("Искорка", "Тиша", "Пуговка", "Кап"):
        assert apply_stress(f"Привет, {name}!") == f"Привет, {name}!"
