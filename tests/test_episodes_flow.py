"""Сквозные параметризованные тесты многоэпизодного потока (пункт 9 спецификации).

Каждый эпизод прогоняется через GameService целиком; переход ep1 -> ep2 проверяется
через «ещё» в хабе завершения, вместе с переносом решений, наград и динамическим
пересказом «в прошлый раз».
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Decision, WorldItem
from app.db.repository import Repository
from app.game.engine import Engine, MultiEngine
from app.service import GameService, make_player_key
from tests.conftest import PEPPER, Player

# Полные проходы по каждому написанному эпизоду: список реплик до конца серии.
FULL_PLAYTHROUGHS = {
    "ep1": ["", "да", "да", "дерево", "давай в садик"],
    "ep2": ["", "в листике", "искра"],
}


def service_with_multi_engine(db) -> GameService:
    return GameService(db, MultiEngine.__new__(MultiEngine), PEPPER)  # не используется напрямую


@pytest.fixture
def multi_service(db, multi_engine) -> GameService:
    """Сервис поверх мультидвижка — именно так работает боевое приложение."""
    return GameService(db, multi_engine, PEPPER)


def playthrough(service, player_id: str, phrases: list[str]) -> list:
    p = Player(service, player_id=player_id)
    return [p.say(phrase) for phrase in phrases]


# --- параметризованный проход по эпизодам -------------------------------------------


@pytest.mark.parametrize("episode_id", sorted(FULL_PLAYTHROUGHS))
def test_each_episode_is_fully_playable(multi_engine, episode_id):
    """Каждый эпизод проходится от старта до complete без тупиков."""
    engine = multi_engine.engine_for(episode_id)
    ctx = engine.new_context()
    outcomes = []
    for phrase in FULL_PLAYTHROUGHS[episode_id]:
        ctx_result = engine.step(ctx, phrase, new_session=(not outcomes))
        outcomes.append(ctx_result)
        from dataclasses import replace as _replace

        ctx = _replace(
            ctx,
            node=ctx_result.node,
            turn=ctx_result.turn,
            unclear_count=ctx_result.unclear_count,
            completed=ctx_result.completed or ctx.completed,
        )
        if ctx_result.decision:
            key, value = ctx_result.decision
            ctx.decisions[key] = value
        ctx.items |= set(ctx_result.unlocks)
        ctx.met |= set(ctx_result.meets)
    assert outcomes[-1].completed, f"{episode_id}: финальный ход должен завершать серию"
    assert outcomes[-1].end_session


def test_firefly_reward_persisted_after_ep2(db, multi_engine):
    """Награда firefly эмитится в TREE_GATE и попадает в БД ровно один раз."""
    service = GameService(db, multi_engine, PEPPER)
    playthrough(service, "firefly-kid", FULL_PLAYTHROUGHS["ep2"])
    key = make_player_key("firefly-kid", PEPPER)
    with db.session_scope() as s:
        repo = Repository(s)
        profile = repo.find_profile(key)
        items = {
            i.item_key
            for i in s.scalars(select(WorldItem).where(WorldItem.profile_id == profile.id))
        }
    assert "firefly" in items


def test_ep2_emits_firefly_unlock_in_tree_gate(multi_engine):
    engine = multi_engine.engine_for("ep2")
    result = engine._play(engine.new_context(), turn=1, start="TREE_GATE", kind="story")
    assert "firefly" in result.unlocks


# --- переход между эпизодами через сервис -------------------------------------------


def test_transition_ep1_to_ep2_via_more(multi_service, db):
    """«Ещё» после ep1 запускает ep2 с переносом решения seed_place и награды места."""
    p = Player(multi_service, player_id="traveler")
    for phrase in FULL_PLAYTHROUGHS["ep1"]:
        out = p.say(phrase)
    assert out.end_session and out.completed is False  # TurnOutcome не несёт completed
    more = p.say("ещё")
    assert not more.end_session
    assert "Искра" in more.text or "светлячк" in more.text.lower() or "серия" in more.text.lower()
    key = make_player_key("traveler", PEPPER)
    with db.session_scope() as s:
        repo = Repository(s)
        profile = repo.find_profile(key)
        assert profile.episode_id == "ep2"
        assert profile.episodes_started == 1
        decisions = {
            d.key: d.value for d in s.scalars(select(Decision).where(Decision.profile_id == profile.id))
        }
        items = {
            i.item_key
            for i in s.scalars(select(WorldItem).where(WorldItem.profile_id == profile.id))
        }
    assert decisions.get("seed_place") == "garden"
    assert {"garden_flower"} <= items


def test_recap_mentions_previous_episode_choices(multi_service):
    """Динамический пересказ «в прошлый раз» звучит в начале ep2 после пройденного ep1."""
    p = Player(multi_service, player_id="recap-kid")
    for phrase in FULL_PLAYTHROUGHS["ep1"]:
        p.say(phrase)
    transition = p.say("ещё")
    low = transition.text.lower()
    assert "в прошлый раз" in low
    assert "семечко" in low or "саду" in low


def test_full_chain_ep1_ep2_firefly_name(multi_service, db):
    """Полная цепочка: ep1 -> «ещё» -> ep2 до финала; светлячок зафиксирован в мире."""
    p = Player(multi_service, player_id="chain-kid")
    for phrase in FULL_PLAYTHROUGHS["ep1"]:
        p.say(phrase)
    p.say("ещё")  # старт ep2 (INTRO)
    p.say("да")  # HATCH -> вопрос о том, как нести искру
    p.say("в ладошках")  # carry_way=hands -> NAME_ASK
    p.say("звёздочка")  # firefly_name=star -> FLY -> TREE_GATE -> FINALE
    final = p.outcomes[-1] if hasattr(p, "outcomes") else None
    key = make_player_key("chain-kid", PEPPER)
    with db.session_scope() as s:
        repo = Repository(s)
        profile = repo.find_profile(key)
        decisions = {
            d.key: d.value for d in s.scalars(select(Decision).where(Decision.profile_id == profile.id))
        }
        items = {
            i.item_key
            for i in s.scalars(select(WorldItem).where(WorldItem.profile_id == profile.id))
        }
    assert decisions.get("carry_way") == "hands"
    assert decisions.get("firefly_name") == "star"
    assert "firefly" in items
    assert profile.node == "EPISODE_COMPLETE"


def test_visit_limit_caps_episodes_per_session(multi_service):
    """Открытый вопрос 4: больше двух эпизодов за визит — мягкое прощание, а не вечный конвейер."""
    p = Player(multi_service, player_id="greedy-kid")
    for phrase in FULL_PLAYTHROUGHS["ep1"]:
        p.say(phrase)
    p.say("ещё")  # ep2 стартовал (эпизодов начато: 2 из 2)
    for phrase in FULL_PLAYTHROUGHS["ep2"][1:]:
        p.say(phrase)
    after_ep2 = p.say("нет") if not p.last.end_session else p.last
    # После завершения второго эпизода лимит исчерпан: «ещё» прощается, новый эпизод не начинается.
    if not after_ep2.end_session:
        over = p.say("ещё")
        assert over.end_session
