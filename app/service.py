"""Сервисный слой: один ход игры = одна транзакция. Связывает движок и БД."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import logging
import threading
from dataclasses import dataclass

from app.audio import _SIL_LEGACY, build_tts, strip_markers
from app.db.database import Database
from app.db.repository import Repository
from app.game.engine import Engine

logger = logging.getLogger("iskorka")

MAX_TEXT = 1024


@dataclass(frozen=True)
class TurnOutcome:
    text: str          # показываемый текст: без пометок и без тегов звуков
    buttons: list[str]
    end_session: bool
    replayed: bool = False  # True, если это повтор уже обработанного запроса
    tts: str = ""      # озвучка: текст + теги <speaker audio=…>; пусто → озвучивается text


_SOUND_TAG = re.compile(r"\s*\[звук:[^\]]*\]")


def strip_sound_tags(text: str) -> str:
    """Убирает пометки «[звук: ...]»: без загруженных звуков Алиса прочитала бы их вслух."""
    return strip_markers(text)


def fit_text(text: str, limit: int = MAX_TEXT) -> str:
    """Страховка от превышения лимита платформы: обрезаем по границе предложения."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    return cut[: end + 1] if end > 0 else cut


def make_player_key(player_id: str, pepper: str) -> str:
    """Псевдоним анонимного игрока. Исходный идентификатор платформы нигде не хранится."""
    return hmac.new(pepper.encode(), f"alice:{player_id}".encode(), hashlib.sha256).hexdigest()


def make_user_key(user_id: str, pepper: str) -> str:
    """Псевдоним авторизованного пользователя (session.user.user_id). Другое пространство имён,
    чем у make_player_key, поэтому ключи не могут совпасть."""
    return hmac.new(pepper.encode(), f"alice-user:{user_id}".encode(), hashlib.sha256).hexdigest()


class GameService:
    def __init__(self, db: Database, engine: Engine, pepper: str) -> None:
        self.db = db
        self.engine = engine
        self.pepper = pepper
        # MVP работает в одном процессе. Замок исключает гонки при параллельных запросах;
        # UNIQUE-ограничения в БД — вторая линия защиты.
        self._lock = threading.Lock()

    def handle_turn(
        self,
        *,
        player_id: str | None,
        session_id: str,
        message_id: int | None,
        utterance: str | None,
        new_session: bool,
        dangerous: bool = False,
        user_id: str | None = None,
    ) -> TurnOutcome:
        """`player_id` — идентификатор приложения (анонимный игрок), `user_id` — подтверждённый
        session.user.user_id. Нужен хотя бы один из них."""
        if not player_id and not user_id:
            raise ValueError("нужен player_id или user_id")
        anon_key = make_player_key(player_id, self.pepper) if player_id else None
        user_key = make_user_key(user_id, self.pepper) if user_id else None
        key = user_key or anon_key
        with self._lock, self.db.session_scope() as session:
            repo = Repository(session)
            ctx0 = self.engine.new_context()
            profile, outcome = repo.resolve_profile(
                anon_key=anon_key, user_key=user_key, episode_id=ctx0.episode_id, start_node=ctx0.node
            )
            self._log_profile_outcome(outcome, profile)

            stored = self._stored_duplicate(profile, session_id, message_id)
            if stored is not None:
                logger.info("Повторный запрос: возвращён сохранённый ответ")
                return stored

            ctx = repo.load_context(profile)
            # Переход между эпизодами сам начинает новую сессию внутри одного визита:
            # следующий ход проигрывает приветствие серии, даже если платформа не прислала `new`.
            # Приветствие нового эпизода уже сыграно в ходе перехода, поэтому ответ ребёнка
            # на следующем ходу — обычный ответ, а не начало сессии (иначе его нужно говорить дважды).
            effective_new = new_session
            if new_session:
                # Лимит «эпизодов за визит» считается заново с каждым новым визитом;
                # эпизод, в котором ребёнок находится сейчас, уже считается начатым.
                profile.episodes_started = 1
                ctx.episodes_started = 1
            result = self.engine.step(ctx, utterance, new_session=effective_new, dangerous=dangerous)
            text = fit_text(strip_sound_tags(result.text))
            tts = fit_text(build_tts(result.text, MAX_TEXT), MAX_TEXT)
            payload = json.dumps(
                {"text": text, "tts": tts, "buttons": result.buttons, "end_session": result.end_session},
                ensure_ascii=False,
            )
            changes = repo.apply_turn(profile, result, session_id, message_id, payload)
            logger.info(
                "Ход: kind=%s node=%s новые_награды=%s", result.kind, result.node, changes["new_items"]
            )
            if result.kind == "unclear":
                # Речь ребёнка не пишем: только где и какая это по счёту неудача.
                logger.info(
                    "Нераспознанный ответ: эпизод=%s узел=%s подряд=%d",
                    profile.episode_id, result.node, result.unclear_count,
                )
            return TurnOutcome(text, result.buttons, result.end_session, tts=tts)

    @staticmethod
    def _log_profile_outcome(outcome: str, profile) -> None:
        """Журнал выбора профиля: без идентификаторов платформы, только внутренний номер профиля."""
        if outcome == "user_linked":
            logger.info("Анонимный профиль привязан к пользователю: профиль=#%s", profile.id)
        elif outcome == "user_existing_conflict":
            logger.info(
                "Конфликт профилей: используется профиль пользователя #%s, анонимный профиль "
                "устройства не изменён и не удалён", profile.id,
            )
        elif outcome == "user_created_anon_taken":
            logger.info("Создан профиль пользователя #%s: анонимный профиль устройства принадлежит другому", profile.id)
        elif outcome in ("user_created", "anon_created"):
            logger.info("Создан профиль #%s (%s)", profile.id, outcome)
        else:
            logger.debug("Профиль #%s найден (%s)", profile.id, outcome)

    @staticmethod
    def _stored_duplicate(profile, session_id: str, message_id: int | None) -> TurnOutcome | None:
        """Запрос с тем же (session_id, message_id) или более старый — это повтор, а не новый ход."""
        if (
            message_id is None
            or profile.last_message_id is None
            or profile.last_session_id != session_id
            or message_id > profile.last_message_id
            or not profile.last_response
        ):
            return None
        data = json.loads(profile.last_response)
        return TurnOutcome(
            data["text"], data["buttons"], data["end_session"], replayed=True,
            # ответы, сохранённые до появления tts; неверную прежнюю запись пауз sil<<мс>> убираем
            tts=_SIL_LEGACY.sub(" ", data.get("tts", data["text"])).strip(),
        )

    def reset_player(self, player_id: str) -> bool:
        """Только для разработки: удаляет профиль и связанные данные."""
        key = make_player_key(player_id, self.pepper)
        with self._lock, self.db.session_scope() as session:
            return Repository(session).delete_profile(key)
