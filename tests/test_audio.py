"""Звуковые эффекты: пометки [звук: …] → теги <speaker audio=…> только в tts."""

from __future__ import annotations

import re

import pytest

from app.audio import MAX_SOUNDS_PER_REPLY, SKILL_ID, SOUNDS, build_tts, speech_markup_stripped, strip_markers
from app.content import episode_ids, load_episode
from app.service import GameService
from tests.conftest import PEPPER, alice_payload
from tests.test_alice_webhook import post

# ID из таблицы «ID Аудио» — единственный допустимый источник идентификаторов.
TABLE_IDS = {
    "6f16edc8-f959-4e15-93a5-d777abc37027", "6242a5ac-e677-4b3f-bb2f-ee139539f043",
    "d78c18a4-7481-4e4b-a3a8-3a7b51e31fa2", "baba5d7c-7648-469d-8cb2-137a01e0074e",
    "6005a60f-7f4a-4980-bf40-370bea26b4b5", "a91e3be6-f70f-4d1f-94a3-8ea211c03882",
    "b29010c9-e99d-4abe-b6f3-339d9d3882b5", "6b1877f9-a22b-4875-b9b6-582d07e5f2a0",
    "1321435f-f7e3-4a71-974b-2e4f4ad73503", "954e9dd8-16d2-4580-a12b-0140892f3fdc",
    "69c9fe3c-8535-4eca-b512-130a74f3d93d", "971043da-1528-4298-afad-2bb52bd08711",
}
TAG = re.compile(r"<speaker audio='dialogs-upload/([0-9a-f-]{36})/([0-9a-f-]{36})\.opus'>")
ANY_TAG = re.compile(r"<speaker[^>]*>")


def test_sound_table_matches_source_ids():
    assert {s.audio_id for s in SOUNDS.values()} == TABLE_IDS
    assert SKILL_ID == "15b0e916-6bfb-4385-b9c6-c35c6e0bca2a"


def test_known_marker_becomes_exact_tag():
    tts = build_tts("[звук: искорка] Привет! Я Искорка.", 1024)
    assert tts == (
        "<speaker audio='dialogs-upload/15b0e916-6bfb-4385-b9c6-c35c6e0bca2a/"
        "6f16edc8-f959-4e15-93a5-d777abc37027.opus'> sil <[200]> Привет! Я Искорка."
    )


def test_unknown_marker_is_dropped_and_text_kept():
    assert build_tts("Ветер. [звук: ветер] Дальше.", 1024) == "Ветер. Дальше."
    assert strip_markers("Ветер. [звук: ветер] Дальше. [звук: тиша]") == "Ветер. Дальше."


def test_same_sound_is_not_repeated_and_count_is_capped():
    tts = build_tts("[звук: тиша] А [звук: тиша] Б [звук: загадка] В [звук: дуб] Г [звук: мостик] Д", 1024)
    tags = TAG.findall(tts)
    assert len(tags) == len(set(tags)) == MAX_SOUNDS_PER_REPLY
    assert speech_markup_stripped(tts).split() == "А Б В Г Д".split()


def test_ambient_sound_is_dropped_first():
    tts = build_tts("[звук: ручей кап] А [звук: тиша] Б [звук: загадка] В [звук: дуб] Г", 1024)
    ids = [t[1] for t in TAG.findall(tts)]
    assert SOUNDS["ручей кап"].audio_id not in ids and len(ids) == 3


def test_tags_dropped_when_tts_would_exceed_limit():
    body = "Слово. " * 140  # ~980 знаков
    tts = build_tts("[звук: искорка] " + body, 1024)
    assert len(tts) <= 1024 and "<speaker" not in tts


def test_webhook_greeting_has_tts_with_sound_and_clean_text(client):
    resp = post(client, alice_payload("", new=True)).json()["response"]
    assert "<speaker" not in resp["text"] and "[звук" not in resp["text"]
    assert "6f16edc8-f959-4e15-93a5-d777abc37027" in resp["tts"]
    assert speech_markup_stripped(resp["tts"]).split() == resp["text"].split()


def test_service_messages_have_plain_tts(client):
    resp = post(client, alice_payload("ping", new=False)).json()["response"]
    assert resp["tts"] == resp["text"] and "<speaker" not in resp["tts"]


@pytest.mark.parametrize("eid", sorted(episode_ids(), key=lambda e: int(e[2:])))
def test_every_marker_in_content_is_known_or_ignorable(eid):
    """Пометка с названием вне таблицы звуков допустима (её вырежут), но формат обязан быть верным."""
    blob = str(load_episode(eid))
    for name in re.findall(r"\[звук: ([^\]]+)\]", blob):
        assert re.fullmatch(r"[а-яё ]+", name)


def test_full_walk_tts_is_valid(db, multi_engine, monkeypatch):
    import tests.test_all_episodes as walk

    seen = []
    real = GameService.handle_turn

    def spy(self, **kw):
        out = real(self, **kw)
        seen.append(out)
        return out

    monkeypatch.setattr(GameService, "handle_turn", spy)
    walk._walk(GameService(db, multi_engine, PEPPER), db, lambda c: c[0])
    assert len(seen) > 100
    used = set()
    for out in seen:
        assert "[звук" not in out.tts and "[звук" not in out.text
        assert "<speaker" not in out.text
        assert len(out.tts) <= 1024
        tags = TAG.findall(out.tts)
        assert len(tags) <= MAX_SOUNDS_PER_REPLY and len(tags) == len(set(tags))
        assert len(ANY_TAG.findall(out.tts)) == len(tags)  # нет «кривых» тегов
        for skill, audio in tags:
            assert skill == SKILL_ID and audio in TABLE_IDS
            used.add(audio)
        # озвучка = показываемый текст + теги: ни одна реплика не потеряна и не продублирована
        assert speech_markup_stripped(out.tts).split() == out.text.split()
    assert len(used) >= 8  # в прохождении реально звучат разные эффекты


def test_stress_marks_only_in_tts_and_only_where_ambiguous():
    raw = "У неё четыре колеса. Обе звезды красивые. Давайте разомнём головы! У него ноги-корни. Просто колёса."
    tts = build_tts(raw, 1024)
    assert "колес+а" in tts and "звезд+ы" in tts and "голов+ы" in tts and "ног+и-корни" in tts
    assert "колёса" in tts and "+" not in tts.replace("колес+а", "").replace("звезд+ы", "").replace("голов+ы", "").replace("ног+и", "")
    assert "+" not in strip_markers(raw)


def test_pauses_use_yandex_syntax_not_the_spoken_form():
    """Формат паузы — `sil <[мс]>`; прежняя запись `sil<<мс>>` Алиса зачитывала вслух."""
    tts = build_tts("[звук: тиша] Раз, два, три! Это ваш выбор. В прошлый раз так: это ваш выбор. [пауза] Пойдём?", 1024)
    assert "sil<<" not in tts and "<<" not in tts and ">>" not in tts
    assert "Раз, sil <[250]> два, sil <[250]> три!" in tts
    assert "это ваш выбор. sil <[300]>" in tts
    assert "sil <[400]> Пойдём?" in tts and "sil <[200]>" in tts  # после звука
    assert strip_markers("Привет. [пауза] Пойдём?") == "Привет. Пойдём?"


def test_sound_dropped_when_it_does_not_fit():
    tts = build_tts("[звук: тиша] " + "Слово. " * 145 + "[пауза] Да?", 1024)
    assert len(tts) <= 1024 and "<speaker" not in tts and "sil" not in tts


def test_pauses_in_full_walk_are_valid_and_never_spoken_form(db, multi_engine, monkeypatch):
    import tests.test_all_episodes as walk

    seen = []
    real = GameService.handle_turn
    monkeypatch.setattr(GameService, "handle_turn", lambda self, **kw: seen.append(real(self, **kw)) or seen[-1])
    walk._walk(GameService(db, multi_engine, PEPPER), db, lambda c: c[0])
    assert len(seen) > 100
    for o in seen:
        assert "<<" not in o.tts and ">>" not in o.tts and "[пауза" not in o.tts + o.text
        assert all(re.fullmatch(r"sil <\[\d+\]>", m) for m in re.findall(r"sil\W*<\W*\d+\W*>", o.tts))
        assert "sil" not in o.text and "<[" not in o.text and "+" not in o.text
        assert speech_markup_stripped(o.tts).split() == o.text.split()
    assert sum("sil <[400]>" in o.tts for o in seen) > 50


def test_replayed_old_response_with_wrong_pause_form_is_cleaned(db, multi_engine):
    """Ответ, сохранённый прежней версией с `sil<<…>>`, при повторе запроса не зачитывается."""
    import json as _json

    from sqlalchemy import select

    from app.db.models import Profile

    svc = GameService(db, multi_engine, PEPPER)
    svc.handle_turn(player_id="p", session_id="s", message_id=0, utterance="", new_session=True)
    with db.session_scope() as s:
        prof = s.scalars(select(Profile)).one()
        data = _json.loads(prof.last_response)
        data["tts"] = "Привет! sil<<400>> Скажи «да». sil <[300]> Ну?"
        prof.last_response = _json.dumps(data, ensure_ascii=False)
    again = svc.handle_turn(player_id="p", session_id="s", message_id=0, utterance="", new_session=True)
    assert again.replayed and "<<" not in again.tts and "sil <[300]>" in again.tts and again.tts.startswith("Привет!")
