"""Звуковые эффекты навыка: пометки `[звук: …]` в сценарии → теги `<speaker audio=…>` в `tts`.

В сценарии (`content/episodes/*.json`) звук обозначается пометкой `[звук: название]`.
Пометки с названиями из `SOUNDS` превращаются в тег воспроизведения загруженного файла;
остальные (колокольчик, ветер, эхо… — файлов для них нет) вырезаются, чтобы Алиса
не прочитала их вслух. В `text` пометок и тегов нет никогда.

Идентификаторы взяты из таблицы «ID Аудио». Путь содержит расширение `.opus`
(так требует формат ссылки Диалогов), хотя исходники — mp3: см. README, раздел «Звуки».
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SKILL_ID = "15b0e916-6bfb-4385-b9c6-c35c6e0bca2a"

# Сколько разных эффектов допустимо в одном ответе (дети 4–8 лет: без перегруза).
MAX_SOUNDS_PER_REPLY = 3


@dataclass(frozen=True)
class Sound:
    name: str        # название, которое пишется в сценарии: [звук: <name>]
    audio_id: str    # ID из таблицы «ID Аудио»
    source: str      # исходное имя файла
    ambient: bool = False  # фоновый эффект: при нехватке места отбрасывается первым


_SOUND_LIST = [
    Sound("искорка", "6f16edc8-f959-4e15-93a5-d777abc37027", "Фирменный-звук-Искорки.mp3"),
    Sound("выбор принят", "6242a5ac-e677-4b3f-bb2f-ee139539f043", "Успешный выбор ребёнка.mp3"),
    Sound("тиша", "d78c18a4-7481-4e4b-a3a8-3a7b51e31fa2", "Тиша — совёнок.mp3"),
    Sound("мостик", "baba5d7c-7648-469d-8cb2-137a01e0074e", "Строительство мостика.mp3"),
    Sound("ручей кап", "6005a60f-7f4a-4980-bf40-370bea26b4b5", "Ручей Кап.mp3", ambient=True),
    Sound("семечко", "a91e3be6-f70f-4d1f-94a3-8ea211c03882", "Появление волшебного семечка.mp3"),
    Sound("новая локация", "b29010c9-e99d-4abe-b6f3-339d9d3882b5", "Открытие новой локации.mp3"),
    Sound("конец приключения", "6b1877f9-a22b-4875-b9b6-582d07e5f2a0", "Мягкое завершение приключения.mp3"),
    Sound("мастерская", "1321435f-f7e3-4a71-974b-2e4f4ad73503", "Мастерская Пуговки.mp3"),
    Sound("лес", "954e9dd8-16d2-4580-a12b-0140892f3fdc", "Лес Долины чудес.mp3", ambient=True),
    Sound("загадка", "69c9fe3c-8535-4eca-b512-130a74f3d93d", "Загадка перед открытием.mp3"),
    Sound("дуб", "971043da-1528-4298-afad-2bb52bd08711", "Великий дуб просыпается.mp3"),
]

SOUNDS: dict[str, Sound] = {s.name: s for s in _SOUND_LIST}

_MARKER = re.compile(r"[ \t]*\[звук:\s*([^\]]*?)\s*\]")
_PAUSE_MARKER = re.compile(r"[ \t]*\[пауза\]")
_PAUSE_MS = 400  # пауза перед вопросом к ребёнку
_SOUND_PAUSE_MS = 200  # короткая пауза после звука, чтобы речь не липла к эффекту
_SPEAKER = re.compile(r"<speaker audio='[^']*'>\s*")


def speaker_tag(sound: Sound) -> str:
    return f"<speaker audio='dialogs-upload/{SKILL_ID}/{sound.audio_id}.opus'>"


def _tidy(text: str) -> str:
    return re.sub(r"\s{2,}", " ", text).strip()


def strip_markers(text: str) -> str:
    """Чистый текст для поля `text`: без пометок и без тегов."""
    return _tidy(_SPEAKER.sub("", _PAUSE_MARKER.sub(" ", _MARKER.sub(" ", text))))


# --- ударения (`+` перед ударной гласной) и паузы для озвучки --------------------------
# Только там, где контекст однозначен: Алиса ошибается в омографах («два колеса́», «две звезды́»).
# Остальные слова она читает верно, лишняя разметка только мешает.
_STRESS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b((?:[Дд]ва|[Тт]ри|[Чч]етыре|[Оо]ба) )колеса\b"), r"\1колес+а"),
    (re.compile(r"\b((?:[Дд]ве|[Тт]ри|[Чч]етыре|[Оо]бе) )звезды\b"), r"\1звезд+ы"),
    (re.compile(r"\b(разомнём )головы\b"), r"\1голов+ы"),
    (re.compile(r"\bноги(?=-корни)"), "ног+и"),
]
# Пауза после пересказа «в прошлый раз» и в счёте «раз, два, три…»
_PAUSES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(это ваш выбор\.)"), r"\1 sil<<300>>"),
    (re.compile(r"\b([Рр]аз|два|три|четыре),(?= (?:два|три|четыре|пять)\b)"), r"\1, sil<<250>>"),
]


def _with_stress(text: str) -> str:
    for rx, repl in _STRESS:
        text = rx.sub(repl, text)
    return text


def _with_pauses(text: str) -> str:
    for rx, repl in _PAUSES:
        text = rx.sub(repl, text)
    return text


def speech_markup_stripped(tts: str) -> str:
    """Озвучка → показываемый текст (для проверок): без тегов, пауз и знаков ударения."""
    tts = _SPEAKER.sub("", tts)
    tts = re.sub(r"\s*sil<<\d+>>\s*", " ", tts)
    return _tidy(tts.replace("+", ""))


def build_tts(raw: str, limit: int) -> str:
    """Текст для озвучки: пометки с известным звуком → теги, остальные вырезаются.

    Правила: один и тот же эффект в ответе не повторяется; не больше MAX_SOUNDS_PER_REPLY;
    при нехватке места первыми отбрасываются фоновые эффекты, затем более поздние.
    Длина озвучки (вместе с тегами) не превышает `limit`.
    """
    raw = _PAUSE_MARKER.sub(" \x00 ", raw)
    pieces: list[tuple[str, Sound | None]] = []
    pos = 0
    seen: set[str] = set()
    for m in _MARKER.finditer(raw):
        pieces.append((raw[pos:m.start()], None))
        sound = SOUNDS.get(m.group(1).strip().lower())
        if sound is not None and sound.name not in seen:
            seen.add(sound.name)
            pieces.append(("", sound))
        pos = m.end()
    pieces.append((raw[pos:], None))

    kept = [i for i, (_, s) in enumerate(pieces) if s is not None]
    # лишнее по числу: фоновые первыми, затем самые поздние
    while len(kept) > MAX_SOUNDS_PER_REPLY:
        kept.remove(_victim(pieces, kept))

    while True:
        tts = _render(pieces, set(kept))
        if len(tts) <= limit:
            return tts
        if not kept:
            return _render(pieces, set(), decorate=False)  # разметка не помещается — чистый текст
        kept.remove(_victim(pieces, kept))


def _victim(pieces, kept: list[int]) -> int:
    ambient = [i for i in kept if pieces[i][1].ambient]
    return (ambient or kept)[-1]


def _render(pieces, kept: set[int], decorate: bool = True) -> str:
    out: list[str] = []
    for i, (text, sound) in enumerate(pieces):
        if sound is not None:
            if i in kept:
                out.append(f" {speaker_tag(sound)} sil<<{_SOUND_PAUSE_MS}>> " if decorate else "")
        else:
            out.append(text)
    body = "".join(out)
    if decorate:
        body = _with_pauses(_with_stress(body)).replace("\x00", f"sil<<{_PAUSE_MS}>>")
    else:
        body = body.replace("\x00", " ")
    return _tidy(body)
