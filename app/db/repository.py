"""Доступ к данным. Все изменения происходят внутри транзакции, которую открывает вызывающий."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    CharacterMeeting,
    Decision,
    EpisodeProgress,
    Profile,
    WorldItem,
)
from app.game.state import GameContext, TurnResult


class Repository:
    def __init__(self, session: Session) -> None:
        self.s = session

    # ------------------------------------------------------------------ профиль

    def get_or_create_profile(self, player_key: str, episode_id: str, start_node: str) -> Profile:
        profile = self.s.scalar(select(Profile).where(Profile.player_key == player_key))
        if profile:
            return profile
        try:
            with self.s.begin_nested():
                profile = Profile(
                    player_key=player_key, episode_id=episode_id, node=start_node, turn=0
                )
                self.s.add(profile)
                self.s.flush()
            return profile
        except IntegrityError:  # параллельный запрос успел создать профиль
            return self.s.scalars(select(Profile).where(Profile.player_key == player_key)).one()

    def find_profile(self, player_key: str) -> Profile | None:
        return self.s.scalar(select(Profile).where(Profile.player_key == player_key))

    def load_context(self, profile: Profile) -> GameContext:
        # Решения берём из всех эпизодов: обратные ссылки поздних эпизодов читают ранние выборы.
        decisions = {
            d.key: d.value
            for d in self.s.scalars(
                select(Decision).where(Decision.profile_id == profile.id).order_by(Decision.id)
            )
        }
        items = set(
            self.s.scalars(select(WorldItem.item_key).where(WorldItem.profile_id == profile.id))
        )
        met = set(
            self.s.scalars(
                select(CharacterMeeting.character_key).where(
                    CharacterMeeting.profile_id == profile.id
                )
            )
        )
        completed = (
            self.s.scalar(
                select(EpisodeProgress.completed_at).where(
                    EpisodeProgress.profile_id == profile.id,
                    EpisodeProgress.episode_id == profile.episode_id,
                )
            )
            is not None
        )
        return GameContext(
            episode_id=profile.episode_id,
            node=profile.node,
            unclear_count=profile.unclear_count,
            turn=profile.turn,
            decisions=decisions,
            items=items,
            met=met,
            completed=completed,
            episodes_started=profile.episodes_started,
        )

    # ------------------------------------------------------------------ применение хода

    def apply_turn(
        self,
        profile: Profile,
        result: TurnResult,
        session_id: str,
        message_id: int | None,
        response_json: str,
    ) -> dict:
        """Сохраняет результат хода. Награды и решения вставляются один раз (UNIQUE + DO NOTHING)."""
        episode_id = profile.episode_id
        new_items: list[str] = []
        new_decision = False

        if result.decision:
            key, value = result.decision
            new_decision = self._insert_once(
                Decision, profile_id=profile.id, episode_id=episode_id, key=key, value=value
            )
        for item in dict.fromkeys(result.unlocks):
            if self._insert_once(
                WorldItem, profile_id=profile.id, item_key=item, episode_id=episode_id
            ):
                new_items.append(item)
        for character in dict.fromkeys(result.meets):
            self._insert_once(CharacterMeeting, profile_id=profile.id, character_key=character)
        if result.completed:
            self._insert_once(EpisodeProgress, profile_id=profile.id, episode_id=episode_id)
            progress = self.s.scalars(
                select(EpisodeProgress).where(
                    EpisodeProgress.profile_id == profile.id,
                    EpisodeProgress.episode_id == episode_id,
                )
            ).one()
            if progress.completed_at is None:
                progress.completed_at = datetime.now(timezone.utc)

        profile.node = result.node
        profile.unclear_count = result.unclear_count
        profile.turn = result.turn
        if result.next_episode_id:
            # Переход между эпизодами: профиль переключается на новый эпизод (пункт 3 спецификации).
            profile.episode_id = result.next_episode_id
            profile.episodes_started += 1
        profile.last_session_id = session_id
        profile.last_message_id = message_id
        profile.last_response = response_json
        self.s.flush()
        return {"new_decision": new_decision, "new_items": new_items}

    def _insert_once(self, model, **values) -> bool:
        """INSERT ... ON CONFLICT DO NOTHING. True, если строка действительно добавлена."""
        stmt = sqlite_insert(model).values(**values).on_conflict_do_nothing()
        return self.s.execute(stmt).rowcount == 1

    # ------------------------------------------------------------------ служебное

    def count(self, model, **filters) -> int:
        stmt = select(func.count()).select_from(model)
        for column, value in filters.items():
            stmt = stmt.where(getattr(model, column) == value)
        return self.s.scalar(stmt) or 0

    def delete_profile(self, player_key: str) -> bool:
        profile = self.find_profile(player_key)
        if not profile:
            return False
        for model in (Decision, WorldItem, CharacterMeeting, EpisodeProgress):
            for row in self.s.scalars(select(model).where(model.profile_id == profile.id)):
                self.s.delete(row)
        self.s.delete(profile)
        return True
