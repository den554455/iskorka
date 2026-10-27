"""Поведение движка на каждом из 30 эпизодов и регрессии, найденные при проверке архива."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import select

from app.content import episode_ids, load_episode
from app.db.models import Decision
from app.db.repository import Repository
from app.game.engine import GIVE_UP_AT, Engine
from app.game.state import GameContext
from app.service import GameService, make_player_key
from tests.conftest import PEPPER, Player

HINTS = {"Подскажи", "Не знаю", "Повтори"}
IDS = sorted(episode_ids(), key=lambda e: int(e[2:]))
ANSWERS = {("ep1", "RIDDLE"): "дерево"}


def engine_for(episode_id: str) -> Engine:
    return Engine(load_episode(episode_id), episode_id)


def apply(ctx: GameContext, r) -> GameContext:
    decisions = dict(ctx.decisions)
    if r.decision:
        decisions.setdefault(*r.decision)
    return replace(
        ctx,
        node=r.node,
        unclear_count=r.unclear_count,
        turn=r.turn,
        decisions=decisions,
        items=set(ctx.items) | set(r.unlocks),
        met=set(ctx.met) | set(r.meets),
        completed=ctx.completed or r.completed,
    )


def play_to_question(episode_id: str, need_decision: bool = False):
    """Ведёт эпизод кнопками до первого вопроса с выбором (или с записью решения).

    Возвращает (engine, ctx перед ответом, результат). Для эпизодов без записываемого решения
    при need_decision=True тест пропускается: у них выбор «украшающий» (ep3, 11, 15, 17, 25, 29).
    """
    engine = engine_for(episode_id)
    ctx = engine.new_context()
    r = engine.step(ctx, "", new_session=True)
    ctx = apply(ctx, r)
    for _ in range(40):
        node = engine.nodes[ctx.node]
        is_choice = bool(set(node.get("expects", {})) - {"yes", "no"}) and ctx.node != "EPISODE_COMPLETE"
        if (node.get("decision") if need_decision else is_choice):
            return engine, ctx, r
        if (episode_id, ctx.node) in ANSWERS:
            reply = ANSWERS[(episode_id, ctx.node)]
        else:
            reply = next((b for b in r.buttons if b not in HINTS), "Да")
        r = engine.step(ctx, reply)
        ctx = apply(ctx, r)
        if ctx.node == "EPISODE_COMPLETE" and need_decision:
            break
    if need_decision:
        pytest.skip(f"{episode_id}: в эпизоде нет записываемого решения")
    pytest.fail(f"{episode_id}: до вопроса с выбором не дошли")


def play_to_decision(episode_id: str):
    return play_to_question(episode_id, need_decision=True)


@pytest.mark.parametrize("episode_id", IDS)
def test_nonsense_ladder_never_traps_child(episode_id):
    """Выбор: мягкое прощание на 5-й попытке. Загадка: на 3-й попытке сюжет идёт дальше сам."""
    engine, ctx, _ = play_to_question(episode_id)
    start_node = ctx.node
    fallback = engine.nodes[start_node]["fallback"]["type"]
    kinds, last = [], None
    for _ in range(GIVE_UP_AT + 2):
        last = engine.step(ctx, "ыыы мммм")
        assert last.decision is None and not last.unlocks, "ответ невпопад ничего не решает и не награждает"
        kinds.append(last.kind)
        if last.end_session or last.node != start_node:
            break
        ctx = apply(ctx, last)
    assert kinds[0] == "unclear"
    if fallback == "advance":
        assert last.node != start_node and not last.end_session, f"{episode_id}: загадка не двинула сюжет: {kinds}"
    else:
        assert last.end_session and last.text, f"{episode_id}: цепочка непонимания не закончилась: {kinds}"


@pytest.mark.parametrize("episode_id", IDS)
def test_simplified_yes_no_makes_decision_after_three_misses(episode_id):
    engine, ctx, _ = play_to_decision(episode_id)
    for _ in range(3):
        r = engine.step(ctx, "ыыы")
        ctx = apply(ctx, r)
    assert "«да»" in r.text.lower() and "«нет»" in r.text.lower()
    done = engine.step(ctx, "да")
    assert done.decision is not None and done.decision[0] == engine.nodes[ctx.node]["decision"]["key"]


@pytest.mark.parametrize("episode_id", IDS)
def test_hint_and_repeat_do_not_advance_or_count_as_failure(episode_id):
    engine, ctx, _ = play_to_question(episode_id)
    hint = engine.step(ctx, "подскажи")
    assert hint.kind == "hint" and hint.node == ctx.node and hint.unclear_count == 0
    assert hint.decision is None and hint.text
    again = engine.step(apply(ctx, hint), "повтори")
    assert again.node == ctx.node and again.decision is None


@pytest.mark.parametrize("episode_id", IDS)
def test_resume_in_new_visit_restores_same_question(episode_id):
    engine, ctx, _ = play_to_question(episode_id)
    back = engine.step(ctx, "", new_session=True)
    assert back.kind == "resume" and back.node == ctx.node
    assert back.decision is None and not back.unlocks and not back.end_session
    assert 0 < len(back.text) <= 1024 and back.buttons
    # после возвращения вопрос по-прежнему можно решить кнопкой: ход идёт дальше, а не по кругу
    button = ANSWERS.get((episode_id, ctx.node)) or next(
        b for b in engine.nodes[ctx.node]["buttons"] if b not in HINTS
    )
    assert engine.step(apply(ctx, back), button).node != ctx.node


@pytest.mark.parametrize("episode_id", IDS)
def test_first_decision_is_never_overwritten(episode_id):
    engine, ctx, _ = play_to_decision(episode_id)
    node = engine.nodes[ctx.node]
    key = node["decision"]["key"]
    values = list(node["decision"]["values"].values())
    ctx = replace(ctx, decisions={**ctx.decisions, key: values[0]})
    r = engine.step(ctx, node["buttons"][-1])
    stored = apply(ctx, r).decisions[key]
    assert stored == values[0]


@pytest.mark.parametrize("episode_id", IDS)
def test_danger_and_exit_are_safe_everywhere(episode_id):
    engine, ctx, _ = play_to_question(episode_id)
    danger = engine.step(ctx, "что-то", dangerous=True)
    assert danger.kind == "danger" and not danger.end_session and danger.node == ctx.node
    bye = engine.step(ctx, "хватит")
    assert bye.end_session and bye.decision is None and bye.node == ctx.node


# --- сервис: регрессии, найденные при проверке ----------------------------------------


@pytest.fixture
def multi(db, multi_engine) -> GameService:
    return GameService(db, multi_engine, PEPPER)


def to_ep1_hub(p: Player) -> None:
    for phrase in ["", "да", "да", "дерево", "в саду"]:
        p.say(phrase)


def profile(db, name: str):
    with db.session_scope() as s:
        pr = Repository(s).find_profile(make_player_key(name, PEPPER))
        return pr.episode_id, pr.node, pr.episodes_started


def test_answer_right_after_transition_is_not_swallowed(multi, db):
    p = Player(multi, "t1")
    to_ep1_hub(p)
    p.say("ещё")
    assert profile(db, "t1")[:2] == ("ep2", "INTRO")
    p.say("да")  # один ответ — один шаг, без повторного приветствия
    assert profile(db, "t1")[:2] == ("ep2", "CARRY_ASK")


def test_visit_limit_resets_in_new_visit_so_month_is_not_blocked(multi, db):
    p = Player(multi, "t2")
    to_ep1_hub(p)
    p.say("ещё")  # ep2
    for phrase in ["да", "в ладошках", "искра"]:
        p.say(phrase)
    assert p.say("ещё").end_session  # лимит визита: два эпизода
    assert profile(db, "t2")[0] == "ep2"
    p.new_session("v2")
    back = p.say("")
    assert not back.end_session
    p.say("ещё")
    assert profile(db, "t2")[0] == "ep3"  # лимит не «накопился» за всё время


def test_duplicate_transition_request_is_idempotent(multi, db):
    p = Player(multi, "t3")
    to_ep1_hub(p)
    kwargs = dict(player_id="t3", session_id="s1", utterance="ещё", new_session=False)
    mid = p.message_id
    first = multi.handle_turn(message_id=mid, **kwargs)
    again = multi.handle_turn(message_id=mid, **kwargs)
    assert again.replayed and again.text == first.text
    assert profile(db, "t3")[:2] == ("ep2", "INTRO")
    assert profile(db, "t3")[2] == 2


def test_recap_is_spoken_at_most_once_per_transition(multi):
    p = Player(multi, "t4")
    to_ep1_hub(p)
    assert p.say("ещё").text.lower().count("в прошлый раз") == 1


def test_help_button_in_episode_28_is_an_answer_not_help():
    engine, ctx, _ = play_to_decision("ep28")
    assert ctx.node == "MAIN_ASK"
    r = engine.step(ctx, "Помощь")
    assert r.kind != "help" and r.decision == ("fifth_branch", "help")


def test_hub_remembers_choice_after_return(multi, db):
    p = Player(multi, "t5")
    to_ep1_hub(p)
    p.say("нет")
    p.new_session("v2")
    assert "саду" in p.say("").text or "сад" in p.say("").text


def test_choices_survive_across_episodes_in_database(multi, db):
    p = Player(multi, "t6")
    to_ep1_hub(p)
    p.say("ещё")
    for phrase in ["да", "в листике", "блёстка"]:
        p.say(phrase)
    with db.session_scope() as s:
        values = {d.key: d.value for d in s.scalars(select(Decision))}
    assert values == {"seed_place": "garden", "carry_way": "leaf", "firefly_name": "glitter"}
