"""Тесты движка без БД и без формата Алисы."""

from __future__ import annotations

import pytest

from app.game.state import GameContext
from tests.conftest import mentions_both_places


def ctx(node: str = "NEW_GAME", **kw) -> GameContext:
    return GameContext(node=node, **kw)


def test_new_game_greets_and_asks_to_help(engine):
    r = engine.step(ctx(), "", new_session=True)
    assert "Искорка" in r.text
    assert "«да»" in r.text
    assert r.node == "INTRO"
    assert r.path == ["NEW_GAME", "INTRO"]
    assert not r.end_session
    assert r.buttons == ["Да", "Нет"]


def test_intro_yes_finds_seed_and_meets_tisha(engine):
    r = engine.step(ctx("INTRO", turn=1), "да")
    assert r.path == ["SEED_FOUND", "LISTEN"]
    assert "семечко" in r.text and "Тиша" in r.text
    assert r.meets == ["tisha"]
    assert r.node == "LISTEN"
    assert r.buttons == ["Да", "Нет"]
    assert r.decision is None and r.unlocks == []


@pytest.mark.parametrize("answer,first", [("да", "LISTEN_YES"), ("нет", "LISTEN_NO")])
def test_listen_scene_leads_to_riddle(engine, answer, first):
    r = engine.step(ctx("LISTEN", turn=2), answer)
    assert r.path == [first, "RIDDLE"]
    assert r.node == "RIDDLE" and r.buttons == ["Подскажи", "Не знаю"]
    assert r.decision is None and r.unlocks == [] and not r.end_session


def test_riddle_correct_answer_walks_to_place_choice(engine):
    r = engine.step(ctx("RIDDLE", turn=3), "дерево")
    assert r.path == ["RIDDLE_OK", "WALK", "CHOOSE_PLACE"]
    assert r.node == "CHOOSE_PLACE" and r.buttons == ["В саду", "У ручья"]
    assert r.decision is None and r.unlocks == [] and not r.end_session


def test_riddle_wrong_answer_gets_gentle_reask_and_keeps_state(engine):
    r = engine.step(ctx("RIDDLE", turn=3), "динозавр")
    assert r.kind == "unclear" and r.node == "RIDDLE"
    assert r.unclear_count == 1
    assert not r.end_session and r.decision is None


def test_riddle_unclear_advances_to_reveal_without_blocking(engine):
    r = engine.step(ctx("RIDDLE", unclear_count=2), "ыыы")
    assert r.path[0] == "RIDDLE_REVEAL" and r.node == "CHOOSE_PLACE"


def test_listen_unclear_advances_to_riddle_without_blocking(engine):
    r = engine.step(ctx("LISTEN", unclear_count=2), "ыыы")
    assert r.path[0] == "RIDDLE" and r.node == "RIDDLE"


def test_intro_no_ends_politely_and_keeps_state(engine):
    r = engine.step(ctx("INTRO"), "нет")
    assert r.end_session and r.node == "INTRO"


@pytest.mark.parametrize(
    "phrase,place,item",
    [
        ("В саду", "garden", "garden_flower"),
        ("Давай в садик", "garden", "garden_flower"),
        ("Посадим у ручья", "stream", "stream_sprout"),
        ("Возле воды", "stream", "stream_sprout"),
    ],
)
def test_choice_phrases_change_world_and_complete_episode(engine, phrase, place, item):
    r = engine.step(ctx("CHOOSE_PLACE"), phrase)
    assert r.decision == ("seed_place", place)
    assert r.unlocks == [item]
    assert r.completed and not r.end_session  # хаб EPISODE_COMPLETE ждёт ответа («ещё»/«нет»)
    assert r.node == "EPISODE_COMPLETE"
    assert r.path[-1] == "EPISODE_COMPLETE"
    assert r.buttons == ["Ещё", "Нет"]


def test_garden_and_stream_have_different_consequences(engine):
    garden = engine.step(ctx("CHOOSE_PLACE"), "сад").text
    stream = engine.step(ctx("CHOOSE_PLACE"), "ручей").text
    assert "цветок" in garden and "росток" not in garden
    assert "росток" in stream and "цветок" not in stream
    assert garden != stream


def test_unclear_answer_gets_gentle_reask_and_keeps_state(engine):
    r = engine.step(ctx("CHOOSE_PLACE"), "бульдозер")
    assert r.kind == "unclear" and r.node == "CHOOSE_PLACE"
    assert r.unclear_count == 1
    assert not r.end_session
    assert mentions_both_places(r.text)
    assert r.decision is None


def test_second_unclear_gives_hint_third_gives_simplified_binary(engine):
    r2 = engine.step(ctx("CHOOSE_PLACE", unclear_count=1), "ыыы")
    assert r2.unclear_count == 2 and ("Подсказка" in r2.text or "Тиша" in r2.text or "Можно" in r2.text)
    r3 = engine.step(ctx("CHOOSE_PLACE", unclear_count=2), "ыыы")
    assert r3.unclear_count == 3
    assert "«да»" in r3.text and "«нет»" in r3.text
    assert r3.buttons == ["Да", "Нет"]


def test_binary_fallback_yes_means_garden_no_means_stream(engine):
    yes = engine.step(ctx("CHOOSE_PLACE", unclear_count=3), "да")
    assert yes.decision == ("seed_place", "garden") and yes.completed
    no = engine.step(ctx("CHOOSE_PLACE", unclear_count=3), "нет")
    assert no.decision == ("seed_place", "stream")


def test_yes_is_not_a_choice_before_simplified_mode(engine):
    r = engine.step(ctx("CHOOSE_PLACE", unclear_count=0), "да")
    assert r.decision is None and r.kind == "unclear"


def test_unclear_in_intro_eventually_advances_story(engine):
    r = engine.step(ctx("INTRO", unclear_count=2), "ыыы")
    assert r.path[0] == "SEED_FOUND" and r.node == "LISTEN"


def test_ambiguous_answer_is_unclear(engine):
    r = engine.step(ctx("CHOOSE_PLACE"), "в саду или у ручья")
    assert r.decision is None and r.kind == "unclear"


def test_hint_request_does_not_count_as_failure(engine):
    r = engine.step(ctx("CHOOSE_PLACE", unclear_count=1), "подскажи")
    assert r.kind == "hint" and r.unclear_count == 1
    assert mentions_both_places(r.text)


def test_repeat_restates_question(engine):
    r = engine.step(ctx("CHOOSE_PLACE"), "повтори")
    assert r.kind == "repeat" and "семечк" in r.text


def test_help_and_about_return_to_current_question(engine):
    h = engine.step(ctx("CHOOSE_PLACE"), "помощь")
    assert h.kind == "help" and "хватит" in h.text and h.node == "CHOOSE_PLACE"
    assert h.text.rstrip().endswith("?") or "сад" in h.text
    a = engine.step(ctx("CHOOSE_PLACE"), "что ты умеешь")
    assert a.kind == "about" and not a.end_session


@pytest.mark.parametrize("node", ["INTRO", "CHOOSE_PLACE", "EPISODE_COMPLETE"])
def test_exit_is_always_available_and_keeps_progress(engine, node):
    r = engine.step(ctx(node, unclear_count=2), "хватит")
    assert r.kind == "exit" and r.end_session and r.node == node


def test_resume_mid_episode_welcomes_back_and_repeats_question(engine):
    r = engine.step(ctx("CHOOSE_PLACE", turn=5), "", new_session=True)
    assert r.kind == "resume" and r.node == "CHOOSE_PLACE"
    assert mentions_both_places(r.text)
    assert not r.end_session


def test_resume_after_completion_mentions_real_consequence(engine):
    garden = engine.step(
        ctx("EPISODE_COMPLETE", decisions={"seed_place": "garden"}, completed=True), "", new_session=True
    )
    stream = engine.step(
        ctx("EPISODE_COMPLETE", decisions={"seed_place": "stream"}, completed=True), "", new_session=True
    )
    assert "саду" in garden.text and "ручь" in stream.text
    assert garden.decision is None and garden.unlocks == [] and not garden.completed


def test_completed_hub_replay_has_no_rewards(engine):
    c = ctx("EPISODE_COMPLETE", decisions={"seed_place": "garden"}, completed=True, turn=9)
    r = engine.step(c, "да")
    assert r.end_session and r.decision is None and r.unlocks == [] and not r.completed
    assert "саду" in r.text


def test_completed_hub_no_says_goodbye(engine):
    r = engine.step(ctx("EPISODE_COMPLETE", decisions={"seed_place": "garden"}), "нет")
    assert r.end_session and r.path == ["BYE"]


def test_existing_decision_is_never_overwritten(engine):
    c = ctx("CHOOSE_PLACE", decisions={"seed_place": "garden"})
    r = engine.step(c, "ручей")
    assert r.decision is None  # не новое решение
    assert r.path[0] == "GARDEN_SCENE"  # ветка соответствует сохранённому выбору


def test_dangerous_context_gets_safe_reply_without_state_change(engine):
    r = engine.step(ctx("CHOOSE_PLACE", unclear_count=1), "что-то", dangerous=True)
    assert r.kind == "danger" and "взрослому" in r.text
    assert r.unclear_count == 1 and r.decision is None


def test_unknown_node_recovers_safely(engine):
    r = engine.step(ctx("OLD_REMOVED_NODE", completed=True, decisions={"seed_place": "garden"}), "", True)
    assert r.node == "EPISODE_COMPLETE"
    r2 = engine.step(ctx("OLD_REMOVED_NODE"), "", True)
    assert r2.node == "INTRO"


def test_engine_is_deterministic(engine):
    a = engine.step(ctx("CHOOSE_PLACE", turn=4), "сад")
    b = engine.step(ctx("CHOOSE_PLACE", turn=4), "сад")
    assert a == b


def test_variants_differ_between_turns(engine):
    texts = {engine.step(ctx("CHOOSE_PLACE", turn=t), "сад").text for t in range(3)}
    assert len(texts) == 3


def test_all_responses_fit_platform_limits(engine):
    for node in engine.nodes:
        for turn in range(3):
            for utterance in ("", "да", "нет", "сад", "ручей", "ыыы", "помощь", "подскажи", "повтори"):
                for new in (False, True):
                    for unclear in range(4):
                        c = ctx(node, turn=turn, unclear_count=unclear, decisions={"seed_place": "stream"})
                        r = engine.step(c, utterance, new_session=new)
                        assert len(r.text) <= 1024, (node, turn, utterance, len(r.text))
                        assert r.text.strip()


def test_endless_unclear_ends_gently_without_decision(engine):
    r = engine.step(ctx("CHOOSE_PLACE", unclear_count=4), "ыыы")
    assert r.end_session and r.decision is None and r.unlocks == [] and not r.completed
    assert r.node == "CHOOSE_PLACE" and r.unclear_count == 0


def test_non_waiting_persisted_node_recovers(engine):
    r = engine.step(ctx("INTRO_NO"), "")
    assert r.node == "INTRO"


def test_reask_and_hint_do_not_repeat_the_question_twice(engine):
    for count in (0, 1):
        r = engine.step(ctx("CHOOSE_PLACE", unclear_count=count), "ыыы")
        assert r.text.count("Где посадим") + r.text.count("Куда посадим") <= 1
        assert r.text.count("Сад или ручей") + r.text.count("в саду или у ручья") <= 1
