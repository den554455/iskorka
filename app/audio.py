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
_SPEAKER = re.compile(r"<speaker audio='[^']*'>\s*")


def speaker_tag(sound: Sound) -> str:
    return f"<speaker audio='dialogs-upload/{SKILL_ID}/{sound.audio_id}.opus'>"


def _tidy(text: str) -> str:
    return re.sub(r"\s{2,}", " ", text).strip()


def strip_markers(text: str) -> str:
    """Чистый текст для поля `text`: без пометок и без тегов."""
    return _tidy(_SPEAKER.sub("", _MARKER.sub(" ", text)))


def build_tts(raw: str, limit: int) -> str:
    """Текст для озвучки: пометки с известным звуком → теги, остальные вырезаются.

    Правила: один и тот же эффект в ответе не повторяется; не больше MAX_SOUNDS_PER_REPLY;
    при нехватке места первыми отбрасываются фоновые эффекты, затем более поздние.
    Длина озвучки (вместе с тегами) не превышает `limit`.
    """
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
        if len(tts) <= limit or not kept:
            return tts
        kept.remove(_victim(pieces, kept))


def _victim(pieces, kept: list[int]) -> int:
    ambient = [i for i in kept if pieces[i][1].ambient]
    return (ambient or kept)[-1]


def _render(pieces, kept: set[int]) -> str:
    out: list[str] = []
    for i, (text, sound) in enumerate(pieces):
        if sound is not None:
            if i in kept:
                out.append(" " + speaker_tag(sound) + " ")
        else:
            out.append(text)
    return _tidy("".join(out))
