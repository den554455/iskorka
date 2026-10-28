"""Проверка структуры сценария. Возвращает список ошибок; пустой список — сценарий корректен."""

from __future__ import annotations

import re
from typing import Any

from app.audio import strip_markers
from app.game.intents import KNOWN_INTENTS

MAX_TEXT = 1024
# Запас под префиксы (приветствие при возвращении, помощь) и разброс символов.
MAX_CHAIN_TEXT = 960
MAX_BUTTON_TITLE = 64
FALLBACK_TYPES = {"advance", "binary", "end"}

# Обращения к ребёнку в прошедшем времени на «ты» выдают род (правило 17 «Правил написания»).
# Проверка намеренно широкая: ловим «ты <слово>», где слово оканчивается на мужское окончание
# причастия/прилагательного или глагола прошедшего времени.
GENDERED_RE = re.compile(
    r"\b(ты|ты\s+)[а-яёa-z]+(лся|лась|лось|ла|ло|л)\b", re.IGNORECASE
)
GENDERED_PATTERNS = (
    # устойчивые обращения из правила 17; проверяются как подстроки словосочетаний
    ("вернулся", "«ты вернулся» выдаёт род — пишите «вот и вы»"),
    ("догадался", "«ты догадался» выдаёт род — пишите «догадались?»"),
    ("поумнел", "выдаёт род"),
    ("смелым", "выдаёт род"),
    ("одним из вас был прав", "выдаёт число"),
)


def _variants(value: Any) -> list[str]:
    """Все варианты текста: список или словарь «значение решения -> список»."""
    if value is None:
        return []
    if isinstance(value, dict):
        out: list[str] = []
        for items in value.values():
            out.extend(items)
        return out
    return list(value)


def _max_len(value: Any) -> int:
    # пометки [звук: …] в показываемый текст не попадают, поэтому длину считаем без них
    return max((len(strip_markers(v)) for v in _variants(value) if isinstance(v, str)), default=0)


def _gendered_problem(text: str) -> list[str]:
    """Находит обращения к ребёнку, выдающие род или число (правило 17)."""
    problems: list[str] = []
    if not isinstance(text, str):
        return problems
    low = text.lower()
    for word, note in GENDERED_PATTERNS:
        if f" ты {word}" in low or f"ты {word}" in low:
            problems.append(note)
    # «ты <причастие/глагол м.р. прош. вр.» — только когда обращение стоит рядом со словом «ты»
    for match in re.finditer(r"ты\s+([а-яё]+)(лся|лась|лось|ла|ло)\b", low):
        stem = match.group(1)
        if stem in ("вернул", "догадал", "остал", "услышал", "попал", "выбрал", "помог"):
            problems.append(f"«ты {stem}{match.group(2)}» выдаёт род (правило 17)")
    return list(dict.fromkeys(problems))


def validate_episode(episode_id: str, ep: dict) -> list[str]:
    errors: list[str] = []
    nodes: dict = ep.get("nodes", {})
    texts: dict = ep.get("texts", {})
    start = ep.get("start")

    def err(msg: str) -> None:
        errors.append(f"[{episode_id}] {msg}")

    if start not in nodes:
        err(f"стартовый узел {start!r} не найден")
        return errors

    for key in ("help", "about", "exit", "resume_prefix", "danger"):
        if not texts.get(key):
            err(f"texts.{key} отсутствует или пуст")

    for key, value in texts.items():
        for problem in _gendered_problem(" ".join(_variants(value))):
            err(f"texts.{key}: {problem}")

    # --- ссылки между узлами и обязательные поля -------------------------------
    edges: dict[str, set[str]] = {name: set() for name in nodes}
    for name, node in nodes.items():
        expects: dict = node.get("expects", {})
        for intent, target in expects.items():
            if intent not in KNOWN_INTENTS:
                err(f"{name}: неизвестное намерение {intent!r}")
            if target not in nodes:
                err(f"{name}: expects[{intent}] ведёт в несуществующий узел {target!r}")
            else:
                edges[name].add(target)

        nxt = node.get("auto_next")
        if nxt is not None:
            if nxt not in nodes:
                err(f"{name}: auto_next ведёт в несуществующий узел {nxt!r}")
            else:
                edges[name].add(nxt)
            if node.get("ask") or expects:
                err(f"{name}: узел с auto_next не должен задавать вопрос")

        persist_as = node.get("persist_as")
        if persist_as:
            if persist_as not in nodes:
                err(f"{name}: persist_as ведёт в несуществующий узел")
            else:
                edges[name].add(persist_as)  # при следующем запуске игра продолжится отсюда

        if node.get("end_session") and expects:
            err(f"{name}: узел завершения сессии не должен ждать ответа")

        if node.get("next_episode"):
            # Узел перехода между эпизодами: движок уходит в следующий эпизод или прощается.
            if not _variants(node.get("say")):
                err(f"{name}: узел перехода next_episode без текста")
            continue

        if not node.get("auto_next") and not node.get("end_session") and not expects:
            err(f"{name}: тупик — нет ни auto_next, ни expects, ни end_session")

        for field in ("say", "ask", "resume", "reask", "hint"):
            for text in _variants(node.get(field)):
                if not isinstance(text, str) or not text.strip():
                    err(f"{name}.{field}: пустой вариант текста")
                elif len(strip_markers(text)) > MAX_TEXT:
                    err(f"{name}.{field}: вариант длиннее {MAX_TEXT} символов")

        # Правило 17: не обращаемся к ребёнку в прошедшем времени на «ты».
        for field in ("say", "ask", "resume", "reask", "hint"):
            for text in _variants(node.get(field)):
                for problem in _gendered_problem(text):
                    err(f"{name}.{field}: {problem}")


        if "by_decision" in node:
            # Хотя бы одно из полей текста должно быть словарём вариантов по значению решения.
            if not any(isinstance(node.get(f), dict) for f in ("say", "resume", "ask", "hint", "reask")):
                err(f"{name}: by_decision задан, но ни одно поле текста не словарь вариантов")

        buttons = node.get("buttons", [])
        for title in buttons:
            if not title or len(title) > MAX_BUTTON_TITLE:
                err(f"{name}: некорректный заголовок кнопки {title!r}")

        # Узел, который ждёт ответа, обязан уметь помочь ребёнку.
        if expects:
            for field in ("ask", "resume", "reask", "hint"):
                if not _variants(node.get(field)):
                    err(f"{name}: у ожидающего узла нет поля {field}")
            if not buttons:
                err(f"{name}: у ожидающего узла нет кнопок-подсказок")
            fallback = node.get("fallback")
            if not fallback or fallback.get("type") not in FALLBACK_TYPES:
                err(f"{name}: нет корректного fallback ({sorted(FALLBACK_TYPES)})")
            else:
                if not _variants(fallback.get("say")):
                    err(f"{name}: fallback без текста")
                if fallback["type"] == "advance" and fallback.get("to") not in nodes:
                    err(f"{name}: fallback.to ведёт в несуществующий узел")
                    edges[name].add(fallback.get("to"))
                elif fallback["type"] == "advance":
                    edges[name].add(fallback["to"])
                if fallback["type"] == "binary":
                    for answer in ("yes", "no"):
                        if fallback.get(answer) not in expects:
                            err(f"{name}: fallback.{answer} не указывает на намерение из expects")
                    if not fallback.get("buttons"):
                        err(f"{name}: у binary fallback нет кнопок")
                    if not _variants(fallback.get("give_up")):
                        err(f"{name}: у binary fallback нет прощального текста give_up")

        decision = node.get("decision")
        if decision:
            values = decision.get("values", {})
            if set(values) != set(expects):
                err(f"{name}: decision.values должны покрывать все намерения expects")

    # --- достижимость ------------------------------------------------------------
    reachable: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in reachable or current not in nodes:
            continue
        reachable.add(current)
        stack.extend(edges.get(current, ()))
    for name in nodes:
        if name not in reachable:
            err(f"узел {name} недостижим из {start}")

    # --- завершение серии --------------------------------------------------------
    # complete на узле с expects (хаб завершения EPISODE_COMPLETE) финалом не считается:
    # движок входит в хаб автоскатом и ждёт ответа, а сессия закрывается по следующему
    # слову ребёнка («нет» -> RECAP/BYE с end_session). Настоящий достижимый финал —
    # complete без вопроса либо end_session-узел.
    if not any(
        nodes[n].get("end_session") or (nodes[n].get("complete") and not nodes[n].get("expects"))
        for n in reachable
    ):
        err("в сценарии нет достижимого финала: ни end_session, ни complete без expects")

    # --- циклы по auto_next (бесконечное автопроигрывание) -----------------------
    for name in nodes:
        seen: set[str] = set()
        current: str | None = name
        while current:
            if current in seen:
                err(f"цикл auto_next через узел {current}")
                break
            seen.add(current)
            current = nodes.get(current, {}).get("auto_next")

    # --- длина самой длинной цепочки озвучки -------------------------------------
    for name in nodes:
        total = 0
        current = name
        guard: set[str] = set()
        while current and current in nodes and current not in guard:
            guard.add(current)
            n = nodes[current]
            total += _max_len(n.get("say")) + _max_len(n.get("ask")) + 2
            current = n.get("auto_next")
        # к цепочке могут добавиться префикс fallback / приветствие при возвращении
        extra = max(
            _max_len(nodes[name].get("fallback", {}).get("say")),
            _max_len(texts.get("resume_prefix")) + _max_len(nodes[name].get("resume")),
            _max_len(texts.get("help")) + _max_len(nodes[name].get("ask")),
        )
        if total > MAX_CHAIN_TEXT:
            err(f"цепочка с началом в {name} может превысить {MAX_CHAIN_TEXT} символов ({total})")
        if extra + _max_len(nodes[name].get("ask")) > MAX_CHAIN_TEXT:
            err(f"узел {name}: служебный ответ может превысить {MAX_CHAIN_TEXT} символов")

    # fallback advance: префикс + цепочка от целевого узла
    for name, node in nodes.items():
        fb = node.get("fallback")
        if fb and fb.get("type") == "advance" and fb.get("to") in nodes:
            total = _max_len(fb.get("say"))
            current = fb["to"]
            guard = set()
            while current and current in nodes and current not in guard:
                guard.add(current)
                total += _max_len(nodes[current].get("say")) + _max_len(nodes[current].get("ask")) + 2
                current = nodes[current].get("auto_next")
            if total > MAX_CHAIN_TEXT:
                err(f"{name}: fallback-цепочка может превысить {MAX_CHAIN_TEXT} символов ({total})")

    return errors


def validate_content(data: dict) -> list[str]:
    errors: list[str] = []
    episodes = data.get("episodes")
    if not isinstance(episodes, dict) or not episodes:
        return ["в сценарии нет раздела episodes"]
    for episode_id, ep in episodes.items():
        errors.extend(validate_episode(episode_id, ep))
    return errors


def _decision_keys_used(episodes: dict) -> dict[str, set[str]]:
    """Все ключи решений, которые встречаются в эпизодах, и значения по умолчанию."""
    used: dict[str, set[str]] = {}
    for ep in episodes.values():
        for node in ep.get("nodes", {}).values():
            decision = node.get("decision")
            if decision and decision.get("key"):
                used.setdefault(decision["key"], set()).update(decision.get("values", {}).values())
            if node.get("by_decision"):
                used.setdefault(node["by_decision"], set())
    return used


def validate_world(episodes: dict, world: dict, series: dict | None = None) -> list[str]:
    """Реестр мира: все ключи решений объявлены, у ссылок есть `_default`, связность серий."""
    errors: list[str] = []
    declared = set(world.get("decision_keys", {}))

    for key in _decision_keys_used(episodes):
        if key not in declared:
            errors.append(f"[world] ключ решения {key!r} не объявлен в world.json")

    for episode_id, ep in episodes.items():
        for name, node in ep.get("nodes", {}).items():
            by = node.get("by_decision")
            if not by:
                continue
            for field in ("say", "resume"):
                value = node.get(field)
                if isinstance(value, dict) and "_default" not in value:
                    errors.append(f"[{episode_id}] {name}.{field}: нет варианта _default для {by!r}")

    order = [eid for arc in series.get("arcs", []) for eid in arc.get("episodes", [])] if series else []
    seen: set[str] = set()
    for eid in order:
        if eid not in episodes:
            continue  # эпизод ещё не написан — это не ошибка сборки
        if eid in seen:
            errors.append(f"[series] эпизод {eid} встречается в порядке дважды")
        seen.add(eid)
        prev = episodes[eid].get("prev")
        index = order.index(eid)
        expected_prev = order[index - 1] if index > 0 else None
        if prev != expected_prev:
            errors.append(
                f"[series] {eid}: prev={prev!r} не совпадает с порядком арки (ожидалось {expected_prev!r})"
            )
    for eid in episodes:
        if eid not in order:
            errors.append(f"[series] эпизод {eid} не включён ни в одну арку series.json")
    return errors
