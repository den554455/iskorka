"""Тесты БД и сервиса: сохранение, восстановление, изоляция, защита от двойных наград."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import CharacterMeeting, Decision, EpisodeProgress, Profile, WorldItem
from app.db.repository import Repository
from app.service import GameService, make_player_key
from tests.conftest import PEPPER, Player, mentions_both_places


def rows(db, model, **filters):
    with db.session_scope() as s:
        return Repository(s).count(model, **filters)


def profile_of(db, player_id: str) -> Profile:
    with db.session_scope() as s:
        return Repository(s).find_profile(make_player_key(player_id, PEPPER))


def play_to_choice(p: Player) -> None:
    p.say("")  # приветствие
    p.say("да")  # согласие помочь -> находка семечка, вопрос «послушаем?»
    p.say("да")  # послушали семечко -> загадка Тиши
    p.say("дерево")  # отгадали загадку -> прогулка по долине и вопрос про место


# --- создание профиля -------------------------------------------------------------


def test_profile_created_on_first_turn_with_hashed_key(service, db):
    p = Player(service)
    p.say("")
    prof = profile_of(db, "player-1")
    assert prof is not None and prof.node == "INTRO"
    assert prof.player_key != "player-1" and len(prof.player_key) == 64
    assert rows(db, Profile) == 1


def test_raw_platform_id_is_not_stored_anywhere(service, db):
    Player(service, player_id="SECRET-APP-ID-123").say("")
    with db.session_scope() as s:
        dump = " ".join(str(v) for v in s.execute(select(Profile)).scalars().all()[0].__dict__.values())
    assert "SECRET-APP-ID-123" not in dump


# --- полная серия -----------------------------------------------------------------


def test_full_episode_garden_end_to_end(service, db):
    p = Player(service)
    first = p.say("")
    assert "Искорка" in first.text and not first.end_session
    second = p.say("да")
    assert "Тиша" in second.text and not second.end_session
    listen = p.say("да")  # послушали семечко -> загадка Тиши
    assert "семечк" in listen.text and not listen.end_session
    riddle_wrong = p.say("динозавр")  # неверная отгадка — мягкий переспрос
    assert not riddle_wrong.end_session and "Кто это" in riddle_wrong.text
    riddle = p.say("дерево")  # отгадали -> вопрос про место
    assert not riddle.end_session and mentions_both_places(riddle.text)
    wrong = p.say("бульдозер")
    assert not wrong.end_session and mentions_both_places(wrong.text)
    hint = p.say("подскажи")
    assert "ручей" in hint.text or "ручья" in hint.text
    final = p.say("давай в садик")
    assert final.end_session

    assert rows(db, Decision) == 1
    assert rows(db, WorldItem, item_key="garden_flower") == 1
    assert rows(db, CharacterMeeting, character_key="tisha") == 1
    assert rows(db, EpisodeProgress) == 1
    assert profile_of(db, "player-1").node == "EPISODE_COMPLETE"


def test_full_episode_stream_end_to_end(service, db):
    p = Player(service)
    play_to_choice(p)
    final = p.say("посадим у ручья")
    assert final.end_session
    with db.session_scope() as s:
        decision = s.scalars(select(Decision)).one()
        assert (decision.key, decision.value) == ("seed_place", "stream")
        assert s.scalars(select(WorldItem)).one().item_key == "stream_sprout"


def test_episode_passable_with_loose_answers_and_simplified_help(service, db):
    """Ребёнок отвечает невпопад, потом выбирает «да» в упрощённом вопросе — серия завершается."""
    p = Player(service)
    p.say("")
    for _ in range(3):  # reask, hint, сюжет двигается сам: ребёнок попадает к вопросу про семечко
        p.say("мммм")
    for _ in range(3):  # то же на вопросе «послушаем семечко?»
        p.say("мммм")
    for _ in range(3):  # то же на загадке Тиши: игра сама раскрывает ответ и ведёт к выбору
        p.say("мммм")
    choice_prompts = [p.say("мммм").text for _ in range(3)]  # reask, hint, binary
    assert "«да»" in choice_prompts[-1] and "«нет»" in choice_prompts[-1]
    final = p.say("угу")
    assert final.end_session
    assert rows(db, EpisodeProgress) == 1
    assert rows(db, Decision) == 1


def test_endless_nonsense_never_traps_child_and_makes_no_decision(service, db):
    p = Player(service)
    p.say("")
    ended = None
    for _ in range(15):
        out = p.say("мммм")
        if out.end_session:
            ended = out
            break
    assert ended is not None, "диалог не должен зацикливаться бесконечно"
    assert rows(db, Decision) == 0 and rows(db, WorldItem) == 0 and rows(db, EpisodeProgress) == 0
    # Прогресс сохранён: в следующий раз ребёнок возвращается к выбору
    p.new_session("s2")
    back = p.say("")
    assert mentions_both_places(back.text) and not back.end_session
    assert p.say("сад").end_session
    assert rows(db, Decision) == 1


# --- восстановление ---------------------------------------------------------------


def test_progress_restored_in_new_session_mid_episode(service, db):
    p = Player(service)
    play_to_choice(p)
    p.new_session("s2")
    back = p.say("")
    assert back.text.startswith(("С возвращением", "Привет-привет", "Ура"))
    assert mentions_both_places(back.text)
    final = p.say("ручей")
    assert final.end_session
    assert rows(db, Decision) == 1


def test_restore_after_crash_new_service_same_database(db, engine):
    """«Сбой»: процесс перезапущен, объекты сервиса созданы заново, состояние берётся из БД."""
    p = Player(GameService(db, engine, PEPPER))
    play_to_choice(p)
    p.say("ыыы")
    restarted = Player(GameService(db, engine, PEPPER), session_id="s-after-restart")
    back = restarted.say("", new_session=True)
    assert "сад" in back.text
    assert restarted.say("в саду").end_session
    assert rows(db, WorldItem, item_key="garden_flower") == 1


def test_relaunch_after_completion_restores_world_without_new_rewards(service, db):
    p = Player(service)
    play_to_choice(p)
    p.say("у ручья")
    p.new_session("s2")
    back = p.say("")
    assert "ручь" in back.text and not back.end_session
    recap = p.say("да")
    assert "росток" in recap.text and recap.end_session
    assert rows(db, Decision) == 1
    assert rows(db, WorldItem) == 1
    assert rows(db, EpisodeProgress) == 1


def test_first_decision_is_not_overwritten(service, db):
    p = Player(service)
    play_to_choice(p)
    p.say("сад")
    # Принудительно возвращаем диалог к выбору (как при ошибке данных) и выбираем другое
    with db.session_scope() as s:
        profile = Repository(s).find_profile(make_player_key("player-1", PEPPER))
        profile.node = "CHOOSE_PLACE"
    p.new_session("s2")
    p.say("")
    again = p.say("ручей")
    assert again.end_session
    with db.session_scope() as s:
        assert s.scalars(select(Decision)).one().value == "garden"
        assert [i.item_key for i in s.scalars(select(WorldItem))] == ["garden_flower"]


# --- повторные запросы ------------------------------------------------------------


def test_duplicate_request_returns_same_answer_and_no_double_reward(service, db):
    p = Player(service)
    play_to_choice(p)  # сообщения 0..3, последний ход — ответ на загадку
    kwargs = dict(player_id="player-1", session_id="s1", utterance="сад", new_session=False)
    first = service.handle_turn(message_id=4, **kwargs)
    again = service.handle_turn(message_id=4, **kwargs)  # повтор после тайм-аута
    assert again.replayed and again.text == first.text
    assert rows(db, WorldItem) == 1 and rows(db, Decision) == 1 and rows(db, EpisodeProgress) == 1


def test_duplicate_with_different_utterance_cannot_overwrite_choice(service, db):
    p = Player(service)
    play_to_choice(p)  # сообщения 0..3
    service.handle_turn(
        player_id="player-1", session_id="s1", message_id=4, utterance="сад", new_session=False
    )
    late = service.handle_turn(
        player_id="player-1", session_id="s1", message_id=4, utterance="ручей", new_session=False
    )
    assert late.replayed
    with db.session_scope() as s:
        assert s.scalars(select(Decision)).one().value == "garden"


def test_stale_out_of_order_request_is_not_reprocessed(service, db):
    p = Player(service)
    play_to_choice(p)  # message_id 0..3
    p.say("сад")  # 4
    stale = service.handle_turn(
        player_id="player-1", session_id="s1", message_id=4, utterance="да", new_session=False
    )
    assert stale.replayed
    assert rows(db, WorldItem) == 1


def test_turn_counter_not_advanced_by_duplicate(service, db):
    p = Player(service)
    p.say("")
    turn = profile_of(db, "player-1").turn
    service.handle_turn(player_id="player-1", session_id="s1", message_id=0, utterance="", new_session=True)
    assert profile_of(db, "player-1").turn == turn


# --- изоляция и ограничения БД -----------------------------------------------------


def test_profiles_are_isolated(service, db):
    a, b = Player(service, "player-A"), Player(service, "player-B", session_id="sb")
    play_to_choice(a)
    play_to_choice(b)
    a.say("сад")
    b.say("ручей")
    with db.session_scope() as s:
        by_player = {}
        for d in s.scalars(select(Decision)):
            by_player[d.profile_id] = d.value
        assert sorted(by_player.values()) == ["garden", "stream"]
    assert rows(db, Profile) == 2
    assert rows(db, WorldItem) == 2
    assert profile_of(db, "player-A").id != profile_of(db, "player-B").id


def test_unique_constraints_block_duplicates_at_db_level(service, db):
    p = Player(service)
    play_to_choice(p)
    p.say("сад")
    prof = profile_of(db, "player-1")
    # награда уже сохранена через INSERT ... ON CONFLICT DO NOTHING (дублей нет);
    # прямой повторный INSERT с теми же ключами блокируется уникальным индексом
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            s.add(WorldItem(profile_id=prof.id, item_key="garden_flower", episode_id="ep1"))
            s.flush()  # flush поднимает IntegrityError внутри открытой транзакции
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            s.add(Decision(profile_id=prof.id, episode_id="ep1", key="seed_place", value="stream"))
            s.flush()


def test_foreign_keys_are_enforced(db):
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            s.add(WorldItem(profile_id=999, item_key="x", episode_id="ep1"))


def test_failed_turn_rolls_back_everything(db, engine, monkeypatch):
    service = GameService(db, engine, PEPPER)
    p = Player(service)
    play_to_choice(p)

    def boom(*a, **k):
        raise RuntimeError("сбой после начала хода")

    monkeypatch.setattr(service.engine, "step", boom)
    with pytest.raises(RuntimeError):
        p.say("сад")
    monkeypatch.undo()
    assert rows(db, Decision) == 0 and rows(db, WorldItem) == 0
    assert profile_of(db, "player-1").node == "CHOOSE_PLACE"


def test_reset_player_removes_all_data(service, db):
    p = Player(service)
    play_to_choice(p)
    p.say("сад")
    assert service.reset_player("player-1") is True
    assert rows(db, Profile) == rows(db, Decision) == rows(db, WorldItem) == rows(db, EpisodeProgress) == 0
    assert service.reset_player("player-1") is False


def test_no_speech_text_is_stored(service, db):
    p = Player(service)
    play_to_choice(p)
    p.say("уникальнаяфразаребёнка")
    with db.session_scope() as s:
        profile = s.scalars(select(Profile)).one()
        dump = " ".join(str(v) for v in profile.__dict__.values())
    assert "уникальнаяфразаребёнка" not in dump
